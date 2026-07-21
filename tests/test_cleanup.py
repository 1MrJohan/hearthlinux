import os
import time

from bgtracker.logwatch.session import prune_old_sessions


def _mkdir_aged(logs, name, age_days):
    d = logs / name
    d.mkdir(parents=True)
    old = time.time() - age_days * 86400
    os.utime(d, (old, old))
    return d


def test_prunes_old_keeps_min_and_newest(tmp_path):
    logs = tmp_path / "Logs"
    dirs = [
        _mkdir_aged(logs, f"Hearthstone_2026_06_{i:02d}_10_00_00", 40 - i)
        for i in range(1, 8)  # 7 dirs, all older than 14 days
    ]
    removed = prune_old_sessions(logs, keep_days=14, keep_min=3)
    survivors = sorted(d.name for d in logs.iterdir())
    assert len(survivors) == 3  # keep_min newest survive despite age
    assert dirs[-1].name in survivors  # newest never touched
    assert len(removed) == 4


def test_recent_dirs_untouched(tmp_path):
    logs = tmp_path / "Logs"
    for i in range(1, 6):
        _mkdir_aged(logs, f"Hearthstone_2026_07_{i:02d}_10_00_00", 2)
    assert prune_old_sessions(logs, keep_days=14, keep_min=2) == []
    assert len(list(logs.iterdir())) == 5


def test_disabled(tmp_path):
    logs = tmp_path / "Logs"
    _mkdir_aged(logs, "Hearthstone_2026_01_01_10_00_00", 200)
    assert prune_old_sessions(logs, keep_days=0, keep_min=1) == []
