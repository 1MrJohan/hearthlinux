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


def prune_old_sessions(logs_dir: Path, keep_days: int, keep_min: int) -> list[Path]:
    """Delete session dirs older than keep_days, always keeping keep_min newest.

    Never touches the newest dir (it may be live). Returns what was removed.
    The parsed match data lives in the history DB; raw logs are disposable.
    """
    import shutil
    import time

    if keep_days <= 0 or not logs_dir.is_dir():
        return []
    dirs = sorted(
        (d for d in logs_dir.iterdir() if d.is_dir() and d.name.startswith(SESSION_PREFIX)),
        key=lambda d: d.name,
        reverse=True,
    )
    cutoff = time.time() - keep_days * 86400
    removed = []
    for d in dirs[max(keep_min, 1):]:
        if d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d)
    return removed
