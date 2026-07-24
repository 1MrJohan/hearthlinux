"""Live tracker status, and the diagnostics bundle.

`status` is a single mutable record the pipeline and the tail loop poke as they
work, so the settings window can answer "is this thing actually running?"
without reaching into either. It is display-only — nothing reads it back to make
a decision, which is why plain attributes are enough.

The bundle exists because the interesting failures are environmental: a Wine
prefix in an unexpected place, a missing `log.config`, a sidecar that will not
start. Reproducing those from a description is hopeless; a single file with the
config, the doctor's verdict, versions and the tail of the log is not.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from bgtracker.config import CACHE_DIR, CONFIG_FILE, DATA_DIR


@dataclass
class Status:
    """What the tracker is doing right now. Display-only."""

    started_at: float = field(default_factory=time.time)
    session: str | None = None
    lines_read: int = 0
    events_seen: int = 0
    last_event: str | None = None
    last_event_at: float | None = None
    sidecar_up: bool | None = None
    sidecar_workers: int | None = None
    last_sim_ms: float | None = None
    last_sims_run: int | None = None
    last_sim_margin: float | None = None

    def note_lines(self, count: int) -> None:
        self.lines_read += count

    def note_event(self, name: str) -> None:
        self.events_seen += 1
        self.last_event = name
        self.last_event_at = time.time()

    def note_sim(self, result) -> None:
        self.last_sim_ms = result.sim_ms
        self.last_sims_run = result.sims_run
        self.last_sim_margin = result.margin

    @property
    def uptime(self) -> float:
        return time.time() - self.started_at

    def rows(self) -> list[tuple[str, str, str | None]]:
        """(label, value, "ok"/"bad"/None) for the settings window."""

        def ago(when: float | None) -> str:
            if when is None:
                return "never"
            gap = time.time() - when
            return f"{gap:.0f}s ago" if gap < 90 else f"{gap / 60:.0f} min ago"

        if self.sidecar_up is None:
            sidecar_text, sidecar_state = "not started", None
        elif self.sidecar_up:
            workers = f" · {self.sidecar_workers} workers" if self.sidecar_workers else ""
            sidecar_text, sidecar_state = f"running{workers}", "ok"
        else:
            sidecar_text, sidecar_state = "unavailable — odds disabled", "bad"

        if self.last_sims_run is None:
            last_sim = "none yet"
        else:
            margin = f" · ±{self.last_sim_margin:.1f}%" if self.last_sim_margin else ""
            last_sim = f"{self.last_sims_run} trials in {self.last_sim_ms:.0f}ms{margin}"

        return [
            ("Uptime", f"{self.uptime / 60:.0f} min", None),
            ("Log session", self.session or "none found", "ok" if self.session else "bad"),
            ("Lines read", f"{self.lines_read:,}", None),
            ("Last event", f"{self.last_event or '—'} · {ago(self.last_event_at)}", None),
            ("Events seen", f"{self.events_seen:,}", None),
            ("Simulator", sidecar_text, sidecar_state),
            ("Last simulation", last_sim, None),
        ]


status = Status()


# -- the bundle ---------------------------------------------------------
def _command(*argv) -> str:
    if shutil.which(argv[0]) is None:
        return f"{argv[0]}: not on PATH"
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).strip()
    except Exception as exc:
        return f"{argv[0]}: {exc}"


def _versions() -> str:
    lines = [
        f"python: {sys.version.split()[0]}",
        f"platform: {platform.platform()}",
        f"node: {_command('node', '--version')}",
    ]
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        lines.append(
            f"gtk: {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
        )
        lines.append(f"pygobject: {gi.__version__}")
    except Exception as exc:
        lines.append(f"gtk: unavailable ({exc})")
    return "\n".join(lines)


def _section(title: str, body: str) -> str:
    return f"\n===== {title} =====\n{body}\n"


async def bundle(cfg=None, extra: str = "") -> Path:
    """Write a diagnostics file and return its path."""
    from bgtracker import doctor, logging_setup

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = CACHE_DIR / f"diagnostics-{stamp}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)

    config_text = (
        CONFIG_FILE.read_text() if CONFIG_FILE.is_file() else "(no config file — all defaults)"
    )
    try:
        records = await doctor.collect(cfg)
        doctor_text = doctor.format_records(records)
    except Exception as exc:
        doctor_text = f"(doctor failed: {exc!r})"

    body = "".join([
        f"hs-bg-tracker diagnostics · {datetime.now().isoformat(timespec='seconds')}\n",
        _section("versions", _versions()),
        _section("paths", "\n".join([
            f"config: {CONFIG_FILE}",
            f"data:   {DATA_DIR}",
            f"cache:  {CACHE_DIR}",
            f"log:    {logging_setup.LOG_FILE}",
        ])),
        _section("config.toml", config_text),
        _section("doctor", doctor_text),
        _section("status", "\n".join(f"{k}: {v}" for k, v, _ in status.rows())),
        _section("recent log", logging_setup.recent(300)),
        _section("notes", extra) if extra else "",
    ])
    path.write_text(body)
    return path
