"""Asyncio client for the Node combat-simulator sidecar.

JSON-lines over stdio; the sidecar is supervised (respawned on crash) and
killed with the parent. One in-flight request at a time is all BG needs.
"""

from __future__ import annotations

import asyncio
import json
import logging
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

    def __str__(self) -> str:
        return (
            f"win {self.won_percent:.0f}% / tie {self.tied_percent:.0f}% / "
            f"loss {self.lost_percent:.0f}%"
        )


class SimClient:
    def __init__(self, sidecar_dir: Path = SIDECAR_DIR, timeout: float = 6.0, sims: int = 8000):
        self.sidecar_dir = sidecar_dir
        self.timeout = timeout
        self.sims = sims
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        if self._proc is None or self._proc.returncode is not None:
            self._proc = await asyncio.create_subprocess_exec(
                "node",
                str(self.sidecar_dir / "server.mjs"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            ready = await asyncio.wait_for(self._proc.stdout.readline(), timeout=60)
            info = json.loads(ready)
            log.info("simulator sidecar ready (v%s)", info.get("simulator"))
        return self._proc

    async def _request(self, payload: dict, timeout: float) -> dict:
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
                if msg.get("id") == self._next_id:
                    if "error" in msg:
                        raise RuntimeError(f"sidecar error: {msg['error']}")
                    return msg["result"]

    async def simulate(self, battle_info: dict) -> SimResult:
        # Give the sidecar a duration budget below our own timeout so it
        # returns a partial-but-valid result instead of us abandoning it.
        battle_info = {
            **battle_info,
            "options": {
                "maxAcceptableDuration": int(self.timeout * 1000 * 0.8),
                **battle_info.get("options", {}),
            },
        }
        result = await self._request(
            {"op": "simulate", "input": battle_info, "sims": self.sims},
            timeout=self.timeout,
        )
        return SimResult(
            won_percent=result["wonPercent"],
            tied_percent=result["tiedPercent"],
            lost_percent=result["lostPercent"],
            avg_damage_won=result["averageDamageWon"],
            avg_damage_lost=result["averageDamageLost"],
        )

    async def ping(self) -> bool:
        try:
            return await self._request({"op": "ping"}, timeout=60) == "pong"
        except Exception:
            return False

    async def close(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except TimeoutError:
                self._proc.kill()
