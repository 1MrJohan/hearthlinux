"""A sidecar that fails to start is retried after a backoff, not given up on.

On 2026-09-30 a system update broke node six minutes before the tracker
started; its one startup ping failed and two games ran without odds, with
nothing that would try again once node was fixed. A fake `node` on PATH
stands in for the broken and the repaired install.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys

import pytest

from bgtracker.sim import client as sim_client
from bgtracker.sim.client import SimClient, SimulatorUnavailable

BROKEN = "#!/bin/sh\necho x >> \"$SPAWNS\"\nexit 127\n"
WORKING = f"""#!{sys.executable}
import json, os, sys
open(os.environ["SPAWNS"], "a").write("x\\n")
print(json.dumps({{"simulator": "fake", "workers": 1}}), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    print(json.dumps({{"id": msg["id"], "result": "pong"}}), flush=True)
"""


@pytest.fixture
def fake_node(tmp_path, monkeypatch):
    spawns = tmp_path / "spawns"
    spawns.touch()
    node = tmp_path / "node"
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SPAWNS", str(spawns))

    def install(script: str) -> None:
        node.write_text(script)
        node.chmod(node.stat().st_mode | stat.S_IEXEC)

    return install, lambda: len(spawns.read_text().split())


def _client() -> SimClient:
    # cpu_policy off: the fake does not care where it runs.
    return SimClient(cpu_policy="off")


def test_a_failed_start_is_not_retried_inside_the_backoff(fake_node):
    install, spawns = fake_node
    install(BROKEN)

    async def run():
        sim = _client()
        assert not await sim.ping()
        with pytest.raises(SimulatorUnavailable):
            await sim._request({"op": "ping"}, timeout=5)
        await sim.close()

    asyncio.run(run())
    assert spawns() == 1, "a broken install must not be respawned per request"


def test_odds_come_back_once_the_install_is_fixed(fake_node, monkeypatch):
    install, spawns = fake_node
    install(BROKEN)

    async def run():
        sim = _client()
        assert not await sim.ping()
        install(WORKING)
        # Past the backoff window.
        now = sim_client.time.monotonic()
        monkeypatch.setattr(sim_client.time, "monotonic",
                            lambda: now + sim_client.RETRY_AFTER_SECONDS + 1)
        assert await sim.ping()
        await sim.close()

    asyncio.run(run())
    assert spawns() == 2
