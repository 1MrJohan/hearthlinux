from pathlib import Path

from bgtracker.discovery import ensure_log_config, wine_prefix_of
from bgtracker.logwatch.session import newest_session_dir


def test_newest_session_dir(tmp_path):
    assert newest_session_dir(tmp_path / "missing") is None
    (tmp_path / "Hearthstone_2026_07_19_10_00_00").mkdir()
    (tmp_path / "Hearthstone_2026_07_20_09_00_00").mkdir()
    (tmp_path / "not-a-session").mkdir()
    assert newest_session_dir(tmp_path).name == "Hearthstone_2026_07_20_09_00_00"


def _fake_prefix(tmp_path: Path) -> Path:
    hs = tmp_path / "drive_c" / "Program Files (x86)" / "Hearthstone"
    hs.mkdir(parents=True)
    (tmp_path / "drive_c" / "users" / "steamuser" / "AppData" / "Local" / "Blizzard").mkdir(parents=True)
    (tmp_path / "drive_c" / "users" / "Public").mkdir(parents=True)
    return hs


def test_ensure_log_config_creates(tmp_path, monkeypatch):
    monkeypatch.setattr("bgtracker.discovery._PREFIX_GLOBS", [])
    hs = _fake_prefix(tmp_path)
    assert wine_prefix_of(hs) == tmp_path
    path, changed = ensure_log_config(hs)
    assert changed
    text = path.read_text()
    assert "steamuser" in str(path)
    assert "[Power]" in text and "[Bob]" in text and "FilePrinting=True" in text
    assert b"\r\n" in path.read_bytes()  # HS client wants CRLF

    # second run: no changes needed
    _, changed = ensure_log_config(hs)
    assert not changed


def test_split_prefix_prefers_game_config_home(tmp_path, monkeypatch):
    """Steam-shortcut setups: install in one prefix, game AppData in another.

    The prefix holding options.txt (game-created) must win over the
    install's own prefix.
    """
    install_prefix = tmp_path / "games" / "battlenet"
    hs = _fake_prefix(install_prefix)

    compat = tmp_path / "compatdata" / "123" / "pfx"
    appdata_hs = compat / "drive_c" / "users" / "steamuser" / "AppData" / "Local" / "Blizzard" / "Hearthstone"
    appdata_hs.mkdir(parents=True)
    (appdata_hs / "options.txt").write_text("gxWindow=1\n")

    monkeypatch.setattr(
        "bgtracker.discovery._PREFIX_GLOBS", [str(tmp_path / "compatdata" / "*" / "pfx")]
    )
    path, changed = ensure_log_config(hs)
    assert changed
    assert path == appdata_hs / "log.config"


def test_ensure_log_config_merges_existing(tmp_path, monkeypatch):
    monkeypatch.setattr("bgtracker.discovery._PREFIX_GLOBS", [])
    hs = _fake_prefix(tmp_path)
    target = tmp_path / "drive_c/users/steamuser/AppData/Local/Blizzard/Hearthstone"
    target.mkdir(parents=True)
    (target / "log.config").write_text("[Zone]\nLogLevel=1\nFilePrinting=true\n")
    path, changed = ensure_log_config(hs)
    assert changed
    text = path.read_text()
    assert "[Zone]" in text and "[Power]" in text and "[Bob]" in text
