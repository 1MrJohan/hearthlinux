"""Entry point: live tracking, fixture replay, and (later) stats.

  python -m bgtracker                    # live headless tracking with odds
  python -m bgtracker --replay FILE      # replay a saved Power.log at full speed
  python -m bgtracker --replay FILE --odds   # replay with combat odds
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from bgtracker import discovery
from bgtracker.config import load_config
from bgtracker.headless import print_event
from bgtracker.logwatch.session import newest_session_dir, power_log_path
from bgtracker.logwatch.tailer import Tailer
from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor
from bgtracker.sim.client import SimClient
from bgtracker.sim.mapper import to_battle_info

log = logging.getLogger("bgtracker")


async def handle_events(events: list[ev.Event], sim: SimClient | None) -> None:
    for event in events:
        print_event(event)
        if sim is not None and isinstance(event, ev.CombatStart):
            info = to_battle_info(event.snapshot)
            if info is None:
                print("  odds: n/a (board incomplete)")
                continue
            try:
                result = await sim.simulate(info)
                print(f"  odds: {result}")
            except Exception as exc:
                log.warning("simulation failed: %s", exc)
                print("  odds: unavailable")


async def start_sim(cfg) -> SimClient | None:
    sim = SimClient(timeout=cfg.sim_timeout, sims=cfg.sim_count)
    if await sim.ping():
        return sim
    log.warning("combat simulator unavailable (node/sidecar missing?) — odds disabled")
    await sim.close()
    return None


async def replay(path: Path, with_odds: bool) -> None:
    cfg = load_config()
    sim = await start_sim(cfg) if with_odds else None
    processor = LiveGameProcessor()
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            batch: list[str] = []
            for line in f:
                batch.append(line.rstrip("\n"))
                if len(batch) >= 2000:
                    await handle_events(processor.feed(batch), sim)
                    batch = []
            await handle_events(processor.feed(batch), sim)
    finally:
        if sim:
            await sim.close()


async def live() -> None:
    cfg = load_config()
    hs_dir = discovery.find_hearthstone_dir(cfg.hearthstone_dir)
    print(f"tracking: {hs_dir}")
    _, changed = discovery.ensure_log_config(hs_dir)
    if changed:
        print("log.config written — restart Hearthstone for logging to take effect")
    sim = await start_sim(cfg)

    logs_dir = hs_dir / "Logs"
    session = None
    tailer = None
    processor = None
    try:
        while True:
            latest = newest_session_dir(logs_dir)
            if latest != session:
                session = latest
                if session:
                    print(f"session: {session.name}")
                    tailer = Tailer(power_log_path(session))
                    processor = LiveGameProcessor()
            if tailer and processor:
                await handle_events(processor.feed(tailer.read_new_lines()), sim)
            await asyncio.sleep(cfg.poll_active)
    finally:
        if sim:
            await sim.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="bgtracker")
    parser.add_argument("--replay", type=Path, help="replay a saved Power.log file")
    parser.add_argument("--odds", action="store_true", help="run combat odds during replay")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.replay:
            asyncio.run(replay(args.replay, args.odds))
        else:
            asyncio.run(live())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
