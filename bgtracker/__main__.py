"""Entry point: live tracking, fixture replay, and (later) stats.

  python -m bgtracker                 # live headless tracking
  python -m bgtracker --replay FILE   # replay a saved Power.log at full speed
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
from bgtracker.parse.exporter import LiveGameProcessor

log = logging.getLogger("bgtracker")


def replay(path: Path) -> None:
    processor = LiveGameProcessor()
    with path.open(encoding="utf-8", errors="replace") as f:
        batch = []
        for line in f:
            batch.append(line.rstrip("\n"))
            if len(batch) >= 2000:
                for event in processor.feed(batch):
                    print_event(event)
                batch = []
        for event in processor.feed(batch):
            print_event(event)


async def live() -> None:
    cfg = load_config()
    hs_dir = discovery.find_hearthstone_dir(cfg.hearthstone_dir)
    print(f"tracking: {hs_dir}")
    _, changed = discovery.ensure_log_config(hs_dir)
    if changed:
        print("log.config written — restart Hearthstone for logging to take effect")

    logs_dir = hs_dir / "Logs"
    session = None
    tailer = None
    processor = None
    while True:
        latest = newest_session_dir(logs_dir)
        if latest != session:
            session = latest
            if session:
                print(f"session: {session.name}")
                tailer = Tailer(power_log_path(session))
                processor = LiveGameProcessor()
        if tailer and processor:
            for event in processor.feed(tailer.read_new_lines()):
                print_event(event)
        await asyncio.sleep(cfg.poll_active)


def main() -> None:
    parser = argparse.ArgumentParser(prog="bgtracker")
    parser.add_argument("--replay", type=Path, help="replay a saved Power.log file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.replay:
        replay(args.replay)
        return
    try:
        asyncio.run(live())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
