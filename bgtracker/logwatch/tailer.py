"""Incremental file tailer.

Reads newline-terminated lines appended to a file, surviving the file not
existing yet, truncation, and partial writes (the trailing unterminated
fragment stays buffered until its newline arrives).

Deliberately synchronous and pull-based: the caller owns the loop, because the
live loop has to interleave reading with re-targeting the install dir and
rotating to a new session. `poll_delay` is the one piece of pacing policy the
caller shares, kept here beside the reader it paces.
"""

from __future__ import annotations

from pathlib import Path

# How many consecutive empty polls count as "nothing is happening", after which
# the caller backs off to its idle interval. At the default 0.25s active poll
# that is five quiet seconds — long enough that it never trips between two
# packets of a live game, short enough to drop off the CPU at the menu.
IDLE_AFTER = 20


def poll_delay(cfg, idle_streak: int) -> float:
    """How long to wait before the next read, given a run of empty polls.

    Read fresh off the shared Config on every pass, which is what makes both
    intervals live settings with nothing to notify (channel NONE).
    """
    return cfg.poll_active if idle_streak < IDLE_AFTER else cfg.poll_idle


class Tailer:
    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._buffer = b""
        try:
            # Bytes already present belong to catch-up.  Keep the boundary
            # stable while the file grows so a bounded drain never mixes old
            # and live input in one batch.
            self._catchup_end = path.stat().st_size
        except FileNotFoundError:
            self._catchup_end = 0
        self.last_read_historical = False
        self.catchup_completed = False
        self.backlogged = False

    def read_new_lines(self, max_bytes: int | None = None) -> list[str]:
        """Synchronously read complete new lines since the last call.

        `max_bytes` bounds one main-loop turn without changing the default API
        for replay/tests.  Per-read flags tell the live loop whether the slice
        is historical, completed catch-up, or left more bytes ready to drain.
        """
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.last_read_historical = False
        self.catchup_completed = False
        self.backlogged = False
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        if size < self._offset:  # truncated / rewritten
            self._offset = 0
            self._buffer = b""
            # Everything currently present was written before we noticed the
            # rewrite.  Treat it like startup catch-up so duplicate combats do
            # not become live simulations.
            self._catchup_end = size
        if size == self._offset:
            return []

        start = self._offset
        historical = start < self._catchup_end
        end = min(size, self._catchup_end) if historical else size
        if max_bytes is not None:
            end = min(end, start + max_bytes)
        with self.path.open("rb") as f:
            f.seek(start)
            chunk = f.read(end - start)
        self._offset = end
        self.last_read_historical = historical
        self.catchup_completed = historical and end >= self._catchup_end
        self.backlogged = end < size
        data = self._buffer + chunk
        *complete, self._buffer = data.split(b"\n")
        return [line.decode("utf-8", errors="replace").rstrip("\r") for line in complete]
