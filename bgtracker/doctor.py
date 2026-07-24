"""`bgtracker doctor` — diagnose the tracking pipeline end to end.

The checks *yield* records rather than printing them, so the same diagnosis can
be rendered in the settings window and folded into a diagnostics bundle. `run()`
is the thin printing shell, and its output is deliberately byte-for-byte what it
always was.
"""

from __future__ import annotations

import asyncio
import shutil

from bgtracker import discovery
from bgtracker.config import CONFIG_FILE, Config, load_config
from bgtracker.logwatch.session import newest_session_dir, power_log_path
from bgtracker.sim.client import SIDECAR_DIR, SimClient

OK = "  [ok] "
BAD = "  [!!] "
INFO = "       "

# Record kinds. `head` and `plain` carry no prefix; the rest are status lines.
PREFIXES = {"ok": OK, "bad": BAD, "info": INFO, "head": "", "plain": ""}


def _check_install(cfg):
    """Records plus the resolved install dir (None if there isn't one).

    Deliberately not a generator: resolving is what logs "Using Hearthstone
    install", so doing it once and handing the answer back keeps that line from
    appearing twice in the output.
    """
    records = [("head", "install:")]
    for hs in discovery.find_hearthstone_dirs():
        records.append(("info", f"found: {hs}"))
    if cfg.hearthstone_dir:
        records.append(("info", f"config override: {cfg.hearthstone_dir}"))
    try:
        hs_dir = discovery.find_hearthstone_dir(cfg.hearthstone_dir)
    except FileNotFoundError as e:
        records.append(("bad", str(e)))
        return records, None
    records.append(("ok", f"using: {hs_dir}"))
    return records, hs_dir


def _check_log_config(hs_dir):
    yield ("head", "logging config:")
    candidates = discovery._candidate_appdata_dirs(hs_dir)
    used = [c for c in candidates if (c / "options.txt").is_file()]
    for c in used:
        yield ("info", f"game config home (has options.txt): {c}")
    if not used:
        yield ("bad", "no game-created AppData dir found (game never run?) — "
                      "log.config target is a guess until first launch")
    target = discovery._appdata_hs_dir(hs_dir) / "log.config"
    if not target.is_file():
        yield ("bad", f"log.config missing at {target} — "
                      "run `python -m bgtracker` once to create it")
        return
    data = target.read_bytes()
    problems = []
    if b"[Power]" not in data:
        problems.append("missing [Power] section")
    if b"FilePrinting=True" not in data:
        problems.append("FilePrinting=True not set")
    if b"\r\n" not in data:
        problems.append("LF-only line endings (client wants CRLF)")
    if problems:
        yield ("bad", f"log.config at {target}: " + "; ".join(problems))
    else:
        yield ("ok", f"log.config good: {target}")


def _check_sessions(hs_dir):
    yield ("head", "log sessions:")
    session = newest_session_dir(hs_dir / "Logs")
    if session is None:
        yield ("bad", f"no session dirs under {hs_dir / 'Logs'} — has the game ever run?")
        return
    yield ("info", f"newest session: {session.name}")
    power = power_log_path(session)
    if power.is_file():
        yield ("ok", f"Power.log present ({power.stat().st_size} bytes)")
    else:
        yield ("bad", "no Power.log in newest session — if the game is running, "
                      "logging is not active (config missing/misplaced at client startup, "
                      "or client not restarted since it was written)")


async def _check_sim(cfg):
    yield ("head", "combat simulator:")
    if shutil.which("node") is None:
        yield ("bad", "node not found on PATH")
        return
    if not (SIDECAR_DIR / "node_modules").is_dir():
        yield ("bad", f"sidecar deps missing — run: cd {SIDECAR_DIR} && npm install")
        return
    sim = SimClient(timeout=cfg.sim_timeout)
    try:
        if await sim.ping():
            yield ("ok", "sidecar responds")
        else:
            yield ("bad", "sidecar did not respond to ping")
    finally:
        await sim.close()


def _check_overlay():
    yield ("head", "overlay:")
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        yield ("ok", "GTK4 + gtk4-layer-shell available")
    except Exception as exc:
        yield ("bad", f"overlay deps unavailable ({exc}) — "
                      "install: sudo pacman -S python-gobject gtk4 gtk4-layer-shell")


async def checks(cfg: Config | None = None):
    """Every diagnostic, as `(kind, text)` records.

    Async because the simulator check has to talk to the sidecar; the settings
    window consumes this directly on the shared loop.
    """
    cfg = cfg if cfg is not None else load_config()
    state = "present" if CONFIG_FILE.is_file() else "defaults"
    yield ("plain", f"config file: {CONFIG_FILE} ({state})")
    # `yield from` is not available inside an async generator.
    install, hs_dir = _check_install(cfg)
    for record in install:
        yield record
    if hs_dir is not None:
        for record in _check_log_config(hs_dir):
            yield record
        for record in _check_sessions(hs_dir):
            yield record
    async for record in _check_sim(cfg):
        yield record
    for record in _check_overlay():
        yield record


def format_records(records) -> str:
    return "\n".join(f"{PREFIXES.get(kind, '')}{text}" for kind, text in records)


async def collect(cfg: Config | None = None) -> list[tuple[str, str]]:
    return [record async for record in checks(cfg)]


def run() -> None:
    for kind, text in asyncio.run(collect()):
        print(f"{PREFIXES.get(kind, '')}{text}")
