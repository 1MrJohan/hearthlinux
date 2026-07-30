"""Foreground and background jobs must not share a worker.

SimClient's request lock holds one job in flight at a time — except on
cancellation, where the shop forecast keeps running in the sidecar while the
combat forecast it yielded to starts. server.mjs's watchdog assumes that never
happens: retiring a stuck worker terminates it, and the `retired` flag then
suppresses the exit handler, so a job sharing that worker would lose shards
silently. Splitting the pool makes the assumption true by construction.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

LANES = Path(__file__).resolve().parent.parent / "sidecar" / "lanes.mjs"


def _lanes(pool_size, reserve):
    """The sidecar's own lane arithmetic, against a stand-in pool.

    Imported from lanes.mjs rather than reimplemented here — a copy of the rule
    in the test would pass no matter what the sidecar actually did.
    """
    code = (
        f"import {{ lanes }} from {str(LANES)!r};"
        f"const pool = Array.from({{length: {pool_size}}}, (_, i) => i);"
        f"console.log(JSON.stringify(lanes(pool, {reserve})));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", code],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_one_reserved_worker_splits_a_pool_of_four():
    assert _lanes(4, 1) == {"fg": [0, 1, 2], "bg": [3]}


def test_the_lanes_never_overlap():
    for pool in (2, 3, 4, 8):
        for reserve in range(1, pool + 2):
            got = _lanes(pool, reserve)
            assert not (set(got["fg"]) & set(got["bg"])), (pool, reserve, got)


def test_no_reservation_leaves_both_lanes_the_whole_pool():
    """reserve=0 is the pre-split behaviour, kept for bisecting."""
    assert _lanes(4, 0) == {"fg": [0, 1, 2, 3], "bg": [0, 1, 2, 3]}


def test_a_single_worker_cannot_reserve_and_the_foreground_keeps_it():
    assert _lanes(1, 1) == {"fg": [0], "bg": [0]}


def test_reserving_everything_still_leaves_the_foreground_a_worker():
    assert _lanes(4, 9)["fg"] == [0]


def test_a_background_job_pools_the_same_odds_as_a_foreground_one():
    """The lane must change which workers run the trials and nothing else.

    Sharding across one worker instead of three is the case where a pooling
    bug hides: percentages that were averaged rather than derived from summed
    counts look right on an even split and wrong on a lopsided one.
    """
    from bgtracker.sim.client import SimClient
    from bgtracker.sim.mapper import to_battle_info

    from .test_mapper import snapshot_from_synthetic

    async def run():
        sim = SimClient(workers=4, shop_workers=1, cpu_policy="off")
        try:
            assert await sim.ping()
            info = to_battle_info(snapshot_from_synthetic())
            fore = await sim.simulate(info, sims=2000)
            back = await sim.simulate(info, sims=2000, background=True)
            return fore, back
        finally:
            await sim.close()

    fore, back = asyncio.run(run())
    # The synthetic 2/3 loses to a 1/7 taunt every time, in either lane.
    assert fore.lost_percent == 100
    assert back.lost_percent == 100
    assert back.sims_run == 2000, "a one-worker lane must still run every trial"
