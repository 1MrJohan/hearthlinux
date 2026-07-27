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
