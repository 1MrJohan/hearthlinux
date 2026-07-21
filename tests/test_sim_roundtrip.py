"""Integration: real Node sidecar. Skipped when node isn't available."""

import asyncio
import shutil

import pytest

from bgtracker.sim.client import SimClient
from bgtracker.sim.mapper import to_battle_info

from .test_mapper import snapshot_from_synthetic

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_winning_board_wins():
    async def run():
        sim = SimClient(sims=2000)
        try:
            assert await sim.ping()
            # our lone 2/3 trades into a 1/7 taunt and dies first -> guaranteed loss
            result = await sim.simulate(to_battle_info(snapshot_from_synthetic()))
            assert result.lost_percent == 100
        finally:
            await sim.close()

    asyncio.run(run())
