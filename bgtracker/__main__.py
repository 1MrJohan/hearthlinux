"""Entry point.

  python -m bgtracker                        # live tracking (odds + history)
  python -m bgtracker --replay FILE          # replay a saved Power.log
  python -m bgtracker --replay FILE --odds --record
  python -m bgtracker stats                  # match history + calibration report
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from bgtracker import discovery, logging_setup
from bgtracker.app import Pipeline
from bgtracker.config import load_config
from bgtracker.diagnostics import status
from bgtracker.data import cards
from bgtracker.history.db import HistoryDB
from bgtracker.logwatch.session import newest_session_dir, power_log_path, prune_old_sessions
from bgtracker.logwatch.tailer import Tailer, poll_delay
from bgtracker.parse.exporter import LiveGameProcessor
from bgtracker.settings import SIM, SIM_RESPAWN, TAILER, SettingsService
from bgtracker.sim.client import SimClient

log = logging.getLogger("bgtracker")


async def start_sim(cfg) -> SimClient | None:
    sim = SimClient(
        timeout=cfg.sim_timeout, sims=cfg.sim_count, workers=cfg.sim_workers,
        cpu_policy=cfg.sim_cpu_policy,
    )
    if await sim.ping():
        status.sidecar_up = True
        status.sidecar_workers = cfg.sim_workers or None
        return sim
    status.sidecar_up = False
    log.warning("combat simulator unavailable (node/sidecar missing?) — odds disabled")
    await sim.close()
    return None


async def replay(settings: SettingsService, path: Path, with_odds: bool, record: bool) -> None:
    cfg = settings.cfg
    sim = await start_sim(cfg) if with_odds else None
    pipeline = Pipeline(sim=sim, db=HistoryDB() if record else None)
    processor = LiveGameProcessor()
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            batch: list[str] = []
            for line in f:
                batch.append(line.rstrip("\n"))
                if len(batch) >= 2000:
                    await pipeline.handle(processor.feed(batch))
                    batch = []
            await pipeline.handle(processor.feed(batch))
    finally:
        if sim:
            await sim.close()


def _target_logs_dir(cfg, strict: bool = False) -> Path | None:
    """Resolve the install dir, switch logging on in it, and prune old sessions.

    Shared by startup and the runtime re-target. `strict` lets the startup path
    fail hard on a missing install (there is nothing to fall back to), while the
    re-target path catches it and keeps tailing wherever it already was.
    """
    try:
        hs_dir = discovery.find_hearthstone_dir(cfg.hearthstone_dir)
    except FileNotFoundError as exc:
        if strict:
            raise
        log.error("cannot track: %s", exc)
        return None
    print(f"tracking: {hs_dir}")
    _, changed = discovery.ensure_log_config(hs_dir)
    if changed:
        print("log.config written — restart Hearthstone for logging to take effect")
    logs_dir = hs_dir / "Logs"
    removed = prune_old_sessions(logs_dir, cfg.log_keep_days, cfg.log_keep_min)
    if removed:
        print(f"pruned {len(removed)} old log session dir(s)")
    return logs_dir


async def live(settings: SettingsService, overlay=None) -> None:
    cfg = settings.cfg
    logs_dir = _target_logs_dir(cfg, strict=True)
    sim = await start_sim(cfg)
    pipeline = Pipeline(sim=sim, db=HistoryDB())
    if overlay is not None:
        overlay.pipeline = pipeline
        pipeline.listeners.append(overlay.on_event)

    if sim is not None:
        settings.subscribe(SIM, lambda _keys: sim.apply_config(cfg))
        settings.subscribe(
            SIM_RESPAWN,
            lambda _keys: settings.spawn(
                sim.reconfigure(cfg.sim_workers, cfg.sim_cpu_policy)
            ),
        )
    # Re-targeting tears down the tailer, which must happen on the loop rather
    # than inside a GTK signal handler — so the applier only raises a flag and
    # the loop below does the work on its next pass.
    retarget = asyncio.Event()
    settings.subscribe(TAILER, lambda _keys: retarget.set())

    session = None
    tailer = None
    processor = None
    session_started = None
    warned_no_log = False
    # Consecutive empty reads. Without this the log is stat()ed four times a
    # second forever, including all the time spent at the menu — and the
    # poll_idle setting, which advertises exactly this behaviour, would do
    # nothing at all.
    idle_streak = 0
    # The first read of a session drains whatever is already in the file, which
    # is by definition history the tracker did not watch happen. Every read
    # after it is live — including the case where Power.log did not exist yet,
    # since a game that starts while we are watching is not catch-up.
    catching_up = True
    try:
        while True:
            if retarget.is_set():
                retarget.clear()
                relocated = _target_logs_dir(cfg)
                if relocated is not None and relocated != logs_dir:
                    logs_dir = relocated
                    # Drop the old session so the block below rebuilds against
                    # the new install, exactly as it does when the game starts
                    # a fresh session.
                    session = tailer = processor = None
            latest = newest_session_dir(logs_dir)
            if latest != session:
                session = latest
                if session:
                    print(f"session: {session.name}")
                    tailer = Tailer(power_log_path(session))
                    processor = LiveGameProcessor()
                    session_started = asyncio.get_running_loop().time()
                    warned_no_log = False
                    idle_streak = 0   # a new session is activity by definition
                    catching_up = True
                    status.session = session.name
            if tailer and processor:
                lines = tailer.read_new_lines()
                idle_streak = 0 if lines else idle_streak + 1
                status.note_lines(len(lines))
                await pipeline.handle(processor.feed(lines), historical=catching_up)
                catching_up = False
                if (
                    not warned_no_log
                    and not tailer.path.exists()
                    and session_started is not None
                    and asyncio.get_running_loop().time() - session_started > 90
                ):
                    warned_no_log = True
                    print(
                        "note: no Power.log yet this session — normal if you haven't "
                        "entered a match. If it stays missing during a game, logging "
                        "is broken: run `python -m bgtracker doctor` and restart Hearthstone."
                    )
            await asyncio.sleep(poll_delay(cfg, idle_streak))
    finally:
        if sim:
            await sim.close()


def _running_instances() -> list[tuple[int, float]]:
    """Other live bgtracker processes as (pid, start time)."""
    import os

    me = os.getpid()
    found = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == me:
                continue
            try:
                argv = entry.joinpath("cmdline").read_bytes().split(b"\0")
                started = entry.stat().st_mtime
            except OSError:
                continue  # process exited, or not ours to read
            # argv[0] must be the interpreter itself, so wrappers that merely
            # mention the module (timeout, sh -c, the shell history) don't count.
            if argv and b"python" in argv[0] and b"bgtracker" in argv:
                found.append((int(entry.name), started))
    except OSError:
        pass  # no /proc; nothing to check
    return found


def _handle_existing_instances(replace: bool) -> None:
    """Replace or flag other live instances.

    Launches are deliberately NON_UNIQUE so a stale instance can never swallow
    a new one, but the flip side is that an old process keeps running the code
    it was started with — editing a file changes nothing about it. Two overlays
    also stack on screen and split clicks between them.
    """
    import os
    import signal

    others = _running_instances()
    if not others:
        return
    if not replace:
        for pid, started in others:
            print(
                f"warning: bgtracker is already running (pid {pid}, started "
                f"{(time.time() - started) / 60:.0f} min ago). It is still "
                f"running the code it launched with. Re-run with --replace to "
                f"take over, or `kill {pid}`.",
                file=sys.stderr,
            )
        return
    for pid, _ in others:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    # Give them a moment to go, so two overlays never share the screen.
    deadline = time.time() + 3.0
    while time.time() < deadline and _running_instances():
        time.sleep(0.05)
    survivors = [pid for pid, _ in _running_instances()]
    if survivors:
        print(f"warning: could not stop pid(s) {survivors}", file=sys.stderr)
    else:
        print(f"replaced {len(others)} running instance(s)")


def run_settings_window(settings: SettingsService) -> None:
    """`bgtracker settings` — the same window, standalone.

    No layer-shell surface is involved, so this path skips the LD_PRELOAD
    re-exec entirely. Changes are written to config.toml; with no tracker
    running there is simply nothing live to push them at.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk

    from bgtracker.overlay import theme
    from bgtracker.overlay.settings_window import SettingsWindow

    app = Gtk.Application(
        application_id="dev.bgtracker.settings",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def activate(_app):
        theme.register_fonts()
        SettingsWindow.open(app, settings)

    app.connect("activate", activate)
    app.run(None)


def run_history_window() -> None:
    """`bgtracker history` — match review, standalone.

    Like the settings window: an ordinary window, no layer shell, no preload.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk

    from bgtracker.overlay import theme
    from bgtracker.overlay.history_window import HistoryWindow

    app = Gtk.Application(
        application_id="dev.bgtracker.history",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def activate(_app):
        theme.register_fonts()
        HistoryWindow.open(app)

    app.connect("activate", activate)
    app.run(None)


def _reexec_with_layer_shell_preload() -> None:
    """gtk4-layer-shell must link before libwayland; from Python that means
    LD_PRELOAD. Re-exec ourselves once with it set."""
    import os

    lib = "/usr/lib/libgtk4-layer-shell.so"
    if os.environ.get("BGTRACKER_PRELOADED") or not os.path.exists(lib):
        return
    env = dict(
        os.environ,
        LD_PRELOAD=lib,
        BGTRACKER_PRELOADED="1",
        PYTHONUNBUFFERED=os.environ.get("PYTHONUNBUFFERED", "1"),
    )
    os.execve(sys.executable, [sys.executable, "-m", "bgtracker", *sys.argv[1:]], env)


def main() -> None:
    parser = argparse.ArgumentParser(prog="bgtracker")
    parser.add_argument(
        "command", nargs="?",
        choices=["run", "stats", "doctor", "mmr", "resim", "settings", "history"],
        default="run",
    )
    parser.add_argument("value", nargs="?", type=int,
                        help="rating for `mmr`; number of recent combats for `resim`")
    parser.add_argument("--replay", type=Path, help="replay a saved Power.log file")
    parser.add_argument("--overlay", action="store_true", help="show the on-screen overlay")
    parser.add_argument("--demo", action="store_true",
                        help="with --overlay: feed the overlay fake data (no game needed)")
    parser.add_argument("--replace", action="store_true",
                        help="stop any bgtracker already running and take over")
    parser.add_argument("--odds", action="store_true", help="run combat odds during replay")
    parser.add_argument("--record", action="store_true", help="write replayed games to history")
    parser.add_argument("--no-names", action="store_true", help="skip card-name DB download")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    # One Config for the whole process, wrapped in the service that mutates it
    # in place. Everything downstream holds this same object, which is what
    # makes a settings change visible without being handed round.
    settings = SettingsService(load_config())
    logging_setup.configure(settings.cfg, verbose=args.verbose)
    logging_setup.attach(settings)

    if not args.no_names:
        cards.load()
    try:
        if args.command == "mmr":
            if args.value is None:
                parser.error("usage: bgtracker mmr <rating>")
            db = HistoryDB()
            db.record_rating(args.value)
            db.close()
            print(f"recorded rating {args.value}")
        elif args.command == "doctor":
            from bgtracker.doctor import run as doctor_run

            doctor_run()
        elif args.command == "settings":
            run_settings_window(settings)
        elif args.command == "history":
            run_history_window()
        elif args.command == "stats":
            from bgtracker.history.stats import report

            print(report())
        elif args.command == "resim":
            from bgtracker.history.resim import resim

            print(asyncio.run(resim(limit=args.value)))
        elif args.replay:
            asyncio.run(replay(settings, args.replay, args.odds, args.record))
        elif args.overlay:
            # After the re-exec, so it is not printed twice.
            _reexec_with_layer_shell_preload()
            _handle_existing_instances(args.replace)
            from bgtracker.overlay.app import OverlayApp

            overlay = OverlayApp(settings)
            if args.demo:
                from bgtracker.overlay import demo

                overlay.run_with(demo.run(overlay))
            else:
                overlay.run_with(live(settings, overlay))
        else:
            _handle_existing_instances(args.replace)
            asyncio.run(live(settings))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
