"""Reading and rewriting config.toml.

The writer is line-based on purpose — stdlib has no TOML writer, and a
round-trip through one would eat the user's hand-written comments. That makes
"comments survive" a behaviour worth pinning down rather than an implementation
detail.

Every test writes to tmp_path. Nothing here may touch the real config file.
"""

from __future__ import annotations

from pathlib import Path

from bgtracker.config import (
    Config,
    load_config,
    remove_config_keys,
    update_config_values,
)

SAMPLE = """\
# My careful notes about scale.
overlay_scale = 1.4
overlay_monitor = "DP-2"

# Calibrated by hand, do not touch.
leaderboard_skew_px = -47
pos_hud_x = 1864
pos_hud_y = 151
"""


def _write(tmp_path: Path, text: str = SAMPLE) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


# -- loading -----------------------------------------------------------
def test_promoted_keys_land_on_real_fields(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg.overlay_scale == 1.4
    assert cfg.overlay_monitor == "DP-2"
    assert cfg.leaderboard_skew_px == -47


def test_panel_positions_stay_in_extra(tmp_path):
    cfg = load_config(_write(tmp_path))
    # pos_* is per-panel, so it cannot be a field; everything else must have
    # been consumed by one.
    assert cfg.extra == {"pos_hud_x": 1864, "pos_hud_y": 151}


def test_unset_keys_keep_their_defaults(tmp_path):
    cfg = load_config(_write(tmp_path))
    assert cfg.sim_count == Config().sim_count
    assert cfg.hover_strips is True


def test_a_missing_file_is_all_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_hearthstone_dir_expands_user(tmp_path):
    path = _write(tmp_path, 'hearthstone_dir = "~/Games/hs"\n')
    assert load_config(path).hearthstone_dir == Path.home() / "Games" / "hs"


def test_an_unknown_future_key_is_preserved_in_extra(tmp_path):
    cfg = load_config(_write(tmp_path, "from_a_newer_version = 3\n"))
    assert cfg.extra == {"from_a_newer_version": 3}


# -- updating ----------------------------------------------------------
def test_update_rewrites_in_place_and_keeps_comments(tmp_path):
    path = _write(tmp_path)
    update_config_values({"overlay_scale": 2.0}, path)
    text = path.read_text()
    assert "overlay_scale = 2.0" in text
    assert "# My careful notes about scale." in text
    assert "# Calibrated by hand, do not touch." in text
    assert load_config(path).overlay_monitor == "DP-2"


def test_update_appends_a_key_the_file_lacks(tmp_path):
    path = _write(tmp_path)
    update_config_values({"sim_count": 4000}, path)
    assert load_config(path).sim_count == 4000


def test_update_creates_the_file_and_its_parent(tmp_path):
    path = tmp_path / "nested" / "config.toml"
    update_config_values({"sim_count": 4000}, path)
    assert load_config(path).sim_count == 4000


def test_booleans_round_trip_as_toml_not_python(tmp_path):
    path = _write(tmp_path, "")
    update_config_values({"hover_debug": True, "log_to_file": False}, path)
    assert "hover_debug = true" in path.read_text()
    cfg = load_config(path)
    assert cfg.hover_debug is True and cfg.log_to_file is False


# -- removing ----------------------------------------------------------
def test_remove_drops_only_the_named_keys(tmp_path):
    path = _write(tmp_path)
    removed = remove_config_keys(["overlay_scale", "leaderboard_skew_px"], path)
    assert sorted(removed) == ["leaderboard_skew_px", "overlay_scale"]
    cfg = load_config(path)
    # Gone means the default is back, which is the whole point of a reset.
    assert cfg.overlay_scale is None
    assert cfg.leaderboard_skew_px == 0
    assert cfg.overlay_monitor == "DP-2"


def test_remove_keeps_comments_and_unrelated_keys(tmp_path):
    path = _write(tmp_path)
    remove_config_keys(["overlay_scale"], path)
    text = path.read_text()
    assert "# My careful notes about scale." in text
    assert "# Calibrated by hand, do not touch." in text
    assert "pos_hud_x = 1864" in text


def test_removing_the_panel_positions_is_a_layout_reset(tmp_path):
    path = _write(tmp_path)
    cfg = load_config(path)
    remove_config_keys([k for k in cfg.extra if k.startswith("pos_")], path)
    assert load_config(path).extra == {}


def test_remove_reports_nothing_when_the_keys_are_absent(tmp_path):
    path = _write(tmp_path)
    before = path.read_text()
    assert remove_config_keys(["sim_count"], path) == []
    assert path.read_text() == before


def test_remove_tolerates_a_missing_file(tmp_path):
    assert remove_config_keys(["sim_count"], tmp_path / "nope.toml") == []


def test_removing_every_line_leaves_a_loadable_empty_file(tmp_path):
    path = _write(tmp_path, "sim_count = 4000\n")
    remove_config_keys(["sim_count"], path)
    assert path.read_text() == ""
    assert load_config(path) == Config()
