"""Integration: the sidecar must survive a board that hangs a worker.

A specific late-game board sends the Firestone simulator into a non-terminating
trial. The worker thread goes CPU-bound and never reports back — no message, no
error, no exit — so the pool's crash recovery never fires. Before the per-job
watchdog, that one stuck worker poisoned *every later request*. Skipped when
node isn't available.

The fixture reproduces an upstream bug at the *pinned* simulator version. If a
future version bump fixes it, this board stops hanging and the test degrades
into a smoke test that no longer exercises the watchdog path — when bumping the
pin, check whether the board still hangs (simulate it alone and watch for the
timeout) and capture a new poison board if not.
"""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from bgtracker.sim.client import SimClient
from bgtracker.sim.mapper import to_battle_info

from .test_mapper import snapshot_from_synthetic

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HANG_BOARD = json.loads((Path(__file__).parent / "fixtures" / "hang_board.json").read_text())


def test_a_hanging_board_does_not_poison_later_requests():
    """One board that hangs a worker must not take down every later forecast.

    The hanging request itself may come back as an error or a partial — what
    matters is that the pool heals, so the *next* board still gets real odds.
    """
    async def run():
        sim = SimClient(timeout=3.0, workers=1)
        try:
            assert await sim.ping()
            # Hangs a worker. However it resolves, it must not leave the pool stuck.
            try:
                await sim.simulate(HANG_BOARD, sims=8000)
            except Exception:
                pass
            # The regression: a normal board must still get odds afterwards.
            result = await sim.simulate(to_battle_info(snapshot_from_synthetic()), sims=2000)
            assert result.lost_percent == 100  # the synthetic 2/3 loses to a 1/7 taunt
        finally:
            await sim.close()

    asyncio.run(run())
