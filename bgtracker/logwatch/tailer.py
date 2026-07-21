"""Asyncio incremental file tailer.

Reads newline-terminated lines appended to a file, surviving the file not
existing yet, truncation, and partial writes (the trailing unterminated
fragment stays buffered until its newline arrives).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path


class Tailer:
    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._buffer = b""

    def read_new_lines(self) -> list[str]:
        """Synchronously read any complete new lines since the last call."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        if size < self._offset:  # truncated / rewritten
            self._offset = 0
            self._buffer = b""
        if size == self._offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self._offset)
            chunk = f.read(size - self._offset)
        self._offset = size
        data = self._buffer + chunk
        *complete, self._buffer = data.split(b"\n")
        return [line.decode("utf-8", errors="replace").rstrip("\r") for line in complete]

    async def follow(
        self,
        poll_active: float = 0.25,
        poll_idle: float = 2.0,
        stop: Callable[[], bool] | None = None,
    ) -> AsyncIterator[list[str]]:
        """Yield batches of new lines; polls fast after activity, slow when idle."""
        idle_streak = 0
        while not (stop and stop()):
            lines = self.read_new_lines()
            if lines:
                idle_streak = 0
                yield lines
            else:
                idle_streak += 1
            await asyncio.sleep(poll_active if idle_streak < 20 else poll_idle)
