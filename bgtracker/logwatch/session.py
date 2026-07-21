"""Track the newest Hearthstone log session directory.

Modern Hearthstone writes each client session's logs into
Logs/Hearthstone_YYYY_MM_DD_HH_MM_SS/. The timestamp format sorts
lexicographically, so "newest" is just max() by name.
"""

from __future__ import annotations

from pathlib import Path

SESSION_PREFIX = "Hearthstone_"


def newest_session_dir(logs_dir: Path) -> Path | None:
    if not logs_dir.is_dir():
        return None
    dirs = [d for d in logs_dir.iterdir() if d.is_dir() and d.name.startswith(SESSION_PREFIX)]
    return max(dirs, key=lambda d: d.name, default=None)


def power_log_path(session_dir: Path) -> Path:
    return session_dir / "Power.log"
