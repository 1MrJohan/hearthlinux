"""Asyncio client for the Node combat-simulator sidecar.

JSON-lines over stdio; the sidecar is supervised (respawned on crash) and
killed with the parent. One in-flight request at a time is all BG needs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SIDECAR_DIR = Path(__file__).resolve().parent.parent.parent / "sidecar"


@dataclass
class SimResult:
    won_percent: float
    tied_percent: float
    lost_percent: float
    avg_damage_won: float
    avg_damage_lost: float
    # Trials the simulator actually reached. NOT the number requested: it
    # abandons the run once maxAcceptableDuration is up, and a busy 7v7 board
    # can come back with a fraction of what was asked for.
    sims_run: int = 0
    sim_ms: float = 0.0
    # Chance the fight ends the game for somebody. The most actionable pair of
    # numbers in Battlegrounds: 12% "you die" outranks any win percentage.
    won_lethal_percent: float = 0.0
    lost_lethal_percent: float = 0.0
    # (min, max) damage at 90% confidence; None when the sidecar didn't supply
    # one (older sidecar, or a pooled multi-worker run).
    damage_won_range: tuple[float, float] | None = None
    damage_lost_range: tuple[float, float] | None = None

    @staticmethod
    def _range_text(average: float, spread: tuple[float, float] | None) -> str:
        if spread is None:
            return f"{average:.0f}"
        low, high = spread
        if round(low) == round(high):
            return f"{low:.0f}"       # every trial landed the same: no spread to show
        return f"{low:.0f}–{high:.0f}"

    @property
    def damage_taken_text(self) -> str:
        return self._range_text(self.avg_damage_lost, self.damage_lost_range)

    @property
    def damage_dealt_text(self) -> str:
        return self._range_text(self.avg_damage_won, self.damage_won_range)

    @property
    def margin(self) -> float | None:
        """95% confidence half-width on the win rate, in percentage points.

        None when nothing ran — no sample is an absence of information, which
        is not the same as a precise answer.
        """
        if self.sims_run <= 0:
            return None
        p = self.won_percent / 100.0
        return 1.96 * math.sqrt(p * (1.0 - p) / self.sims_run) * 100.0

    def __str__(self) -> str:
        return (
            f"win {self.won_percent:.0f}% / tie {self.tied_percent:.0f}% / "
            f"loss {self.lost_percent:.0f}%"
        )


def _range(raw: dict | None) -> tuple[float, float] | None:
    """The sim reports `null` for a side that never won a single trial."""
    if not raw:
        return None
    return (raw["min"], raw["max"])


def _to_result(result: dict, elapsed_ms: float) -> SimResult:
    """Build a SimResult from a sidecar payload — partial or final alike."""
    return SimResult(
        won_percent=result["wonPercent"],
        tied_percent=result["tiedPercent"],
        lost_percent=result["lostPercent"],
        avg_damage_won=result["averageDamageWon"],
        avg_damage_lost=result["averageDamageLost"],
        # The raw counts are the only report of how far the run actually got
        # before the duration budget cut it off.
        sims_run=result["won"] + result["tied"] + result["lost"],
        sim_ms=elapsed_ms,
        won_lethal_percent=result.get("wonLethalPercent") or 0.0,
        lost_lethal_percent=result.get("lostLethalPercent") or 0.0,
        damage_won_range=_range(result.get("damageWonRange")),
        damage_lost_range=_range(result.get("damageLostRange")),
    )


class SimClient:
    def __init__(
        self,
        sidecar_dir: Path = SIDECAR_DIR,
        timeout: float = 6.0,
        sims: int = 8000,
        workers: int = 0,
    ):
        self.sidecar_dir = sidecar_dir
        self.timeout = timeout
        self.sims = sims
        # 0 leaves the choice to the sidecar's own default.
        self.workers = workers
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        if self._proc is None or self._proc.returncode is not None:
            env = None
            if self.workers:
                env = {**os.environ, "BGTRACKER_SIM_WORKERS": str(self.workers)}
            self._proc = await asyncio.create_subprocess_exec(
                "node",
                str(self.sidecar_dir / "server.mjs"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            ready = await asyncio.wait_for(self._proc.stdout.readline(), timeout=60)
            info = json.loads(ready)
            log.info(
                "simulator sidecar ready (v%s, %s worker(s))",
                info.get("simulator"), info.get("workers", 1),
            )
        return self._proc

    async def _request(self, payload: dict, timeout: float, on_partial=None) -> dict:
        async with self._lock:
            proc = await self._ensure_proc()
            self._next_id += 1
            payload["id"] = self._next_id
            proc.stdin.write(json.dumps(payload).encode() + b"\n")
            await proc.stdin.drain()
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if not line:
                    raise RuntimeError("sidecar died mid-request")
                msg = json.loads(line)
                if msg.get("id") != self._next_id:
                    continue
                if "error" in msg:
                    raise RuntimeError(f"sidecar error: {msg['error']}")
                # Progress updates keep arriving under the same id until the
                # final `result` line closes the request out.
                if "partial" in msg:
                    if on_partial is not None:
                        on_partial(_to_result(msg["partial"], 0.0))
                    continue
                return msg["result"]

    async def simulate(self, battle_info: dict, on_partial=None, sims: int | None = None) -> SimResult:
        """Run a combat.

        `on_partial`, if given, is called with a provisional SimResult each time
        the run tightens — a usable number lands in a fraction of the time the
        full run takes, which matters on the boards that take seconds.
        """
        # Give the sidecar a duration budget below our own timeout so it
        # returns a partial-but-valid result instead of us abandoning it.
        battle_info = {
            **battle_info,
            "options": {
                "maxAcceptableDuration": int(self.timeout * 1000 * 0.8),
                **battle_info.get("options", {}),
            },
        }
        started = time.perf_counter()
        result = await self._request(
            {"op": "simulate", "input": battle_info, "sims": sims or self.sims},
            timeout=self.timeout,
            on_partial=on_partial,
        )
        return _to_result(result, (time.perf_counter() - started) * 1000.0)

    async def ping(self) -> bool:
        try:
            return await self._request({"op": "ping"}, timeout=60) == "pong"
        except Exception:
            return False

    # -- live reconfiguration -------------------------------------------
    def apply_config(self, cfg) -> None:
        """Adopt trial count and timeout. Both are read fresh per `simulate()`,
        so assignment is the entire mechanism — no restart, nothing in flight
        disturbed."""
        self.sims = cfg.sim_count
        self.timeout = cfg.sim_timeout

    async def reconfigure_workers(self, workers: int) -> bool:
        """Restart the sidecar on a new worker count. Returns whether it moved.

        The worker count reaches the sidecar only as an environment variable at
        spawn (`BGTRACKER_SIM_WORKERS`), so it genuinely cannot change without a
        restart.

        **The lock is the point.** `_request` holds `self._lock` across its
        `readline()` await, so taking it here means a worker change queues
        behind any combat forecast already in flight instead of killing the
        process under it — which would raise "sidecar died mid-request" and
        leave that fight with no odds at all. Respawning eagerly inside the
        lock also pays the multi-second card-DB rebuild now, while the user is
        looking at the settings window, rather than charging it to the next
        combat's timeout.
        """
        if workers == self.workers:
            return False
        async with self._lock:
            self.workers = workers
            await self._stop()
            await self._ensure_proc()
        return True

    async def _stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except TimeoutError:
                self._proc.kill()
        self._proc = None

    async def close(self) -> None:
        await self._stop()
