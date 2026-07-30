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
        # shop_workers=0: this test issues no background job, so a nonzero
        # reservation would just take a worker away from it for nothing.
        sim = SimClient(sims=2000, shop_workers=0)
        try:
            assert await sim.ping()
            # our lone 2/3 trades into a 1/7 taunt and dies first -> guaranteed loss
            result = await sim.simulate(to_battle_info(snapshot_from_synthetic()))
            assert result.lost_percent == 100
            assert result.sims_run == 2000, "every requested trial should have run"
        finally:
            await sim.close()

    asyncio.run(run())


def test_sharding_across_workers_does_not_change_the_answer():
    """Percentages come from pooled counts, never from averaged shards.

    Averaging the workers' percentages would weight a shard that stopped early
    exactly as heavily as one that finished, quietly biasing the result.
    """
    async def run(workers):
        # shop_workers=0 so the requested `workers` count is the whole
        # foreground lane, not the pool minus a background reservation this
        # test never uses.
        sim = SimClient(sims=2000, workers=workers, shop_workers=0)
        try:
            result = await sim.simulate(to_battle_info(snapshot_from_synthetic()))
            return result.lost_percent, result.sims_run
        finally:
            await sim.close()

    assert asyncio.run(run(1)) == asyncio.run(run(4)) == (100, 2000)


def test_partial_results_only_ever_grow():
    """Partials are a run tightening, not independent samples.

    A partial that reported fewer trials than one before it, or more than the
    final, would mean shard bookkeeping is double-counting.
    """
    async def run():
        sim = SimClient(sims=40000, timeout=60.0, workers=2, shop_workers=0)
        seen: list[int] = []
        try:
            final = await sim.simulate(
                to_battle_info(snapshot_from_synthetic()),
                on_partial=lambda p: seen.append(p.sims_run),
            )
        finally:
            await sim.close()
        return seen, final

    seen, final = asyncio.run(run())
    assert seen == sorted(seen), f"partials went backwards: {seen}"
    assert all(n <= final.sims_run for n in seen), f"partial exceeded final: {seen}"
