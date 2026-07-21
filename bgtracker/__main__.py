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
from pathlib import Path

from bgtracker import discovery
from bgtracker.app import Pipeline
from bgtracker.config import load_config
from bgtracker.data import cards
from bgtracker.history.db import HistoryDB
from bgtracker.logwatch.session import newest_session_dir, power_log_path
from bgtracker.logwatch.tailer import Tailer
from bgtracker.parse.exporter import LiveGameProcessor
from bgtracker.sim.client import SimClient

log = logging.getLogger("bgtracker")


async def start_sim(cfg) -> SimClient | None:
    sim = SimClient(timeout=cfg.sim_timeout, sims=cfg.sim_count)
    if await sim.ping():
        return sim
    log.warning("combat simulator unavailable (node/sidecar missing?) — odds disabled")
    await sim.close()
    return None


async def replay(path: Path, with_odds: bool, record: bool) -> None:
    cfg = load_config()
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


async def live(overlay=None) -> None:
    cfg = load_config()
    hs_dir = discovery.find_hearthstone_dir(cfg.hearthstone_dir)
    print(f"tracking: {hs_dir}")
    _, changed = discovery.ensure_log_config(hs_dir)
    if changed:
        print("log.config written — restart Hearthstone for logging to take effect")
    sim = await start_sim(cfg)
    pipeline = Pipeline(sim=sim, db=HistoryDB())
    if overlay is not None:
        overlay.pipeline = pipeline
        pipeline.listeners.append(overlay.on_event)

    logs_dir = hs_dir / "Logs"
    session = None
    tailer = None
    processor = None
    session_started = None
    warned_no_log = False
    try:
        while True:
            latest = newest_session_dir(logs_dir)
            if latest != session:
                session = latest
                if session:
                    print(f"session: {session.name}")
                    tailer = Tailer(power_log_path(session))
                    processor = LiveGameProcessor()
                    session_started = asyncio.get_running_loop().time()
                    warned_no_log = False
            if tailer and processor:
                await pipeline.handle(processor.feed(tailer.read_new_lines()))
                if (
                    not warned_no_log
                    and not tailer.path.exists()
                    and session_started is not None
                    and asyncio.get_running_loop().time() - session_started > 90
                ):
                    warned_no_log = True
                    print(
                        "WARNING: session is 90s old but Power.log has not appeared — "
                        "power logging is not active. Run `python -m bgtracker doctor`, "
                        "then fully restart Hearthstone."
                    )
            await asyncio.sleep(cfg.poll_active)
    finally:
        if sim:
            await sim.close()


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
    parser.add_argument("command", nargs="?", choices=["run", "stats", "doctor"], default="run")
    parser.add_argument("--replay", type=Path, help="replay a saved Power.log file")
    parser.add_argument("--overlay", action="store_true", help="show the on-screen overlay")
    parser.add_argument("--odds", action="store_true", help="run combat odds during replay")
    parser.add_argument("--record", action="store_true", help="write replayed games to history")
    parser.add_argument("--no-names", action="store_true", help="skip card-name DB download")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not args.no_names:
        cards.load()
    try:
        if args.command == "doctor":
            from bgtracker.doctor import run as doctor_run

            doctor_run()
        elif args.command == "stats":
            from bgtracker.history.stats import report

            print(report())
        elif args.replay:
            asyncio.run(replay(args.replay, args.odds, args.record))
        elif args.overlay:
            _reexec_with_layer_shell_preload()
            from bgtracker.overlay.app import OverlayApp

            overlay = OverlayApp()
            overlay.run_with(live(overlay))
        else:
            asyncio.run(live())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
