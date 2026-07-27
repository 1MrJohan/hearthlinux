"""The settings schema must stay in step with Config and with the example file.

The whole point of the schema is that adding a setting is one edit in each of
two places and zero anywhere else. These tests are what enforce that: they fail
loudly when a Config field gains a setting with no schema entry, when the two
disagree about a default, or when `config.example.toml` drifts from the schema
that generates it.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from bgtracker import settings
from bgtracker.config import Config

EXAMPLE_FILE = Path(__file__).resolve().parent.parent / "config.example.toml"


def _field_defaults() -> dict:
    return {
        f.name: f.default
        for f in dataclasses.fields(Config)
        if f.name != "extra"
    }


def test_every_config_field_has_a_setting():
    unschemad, unfielded = settings.missing_settings()
    assert unschemad == (), f"Config fields with no Setting entry: {unschemad}"
    assert unfielded == (), f"Settings with no Config field: {unfielded}"


@pytest.mark.parametrize("setting", settings.SETTINGS, ids=lambda s: s.key)
def test_schema_default_matches_the_dataclass_default(setting):
    # A disagreement here means the settings window would show one default and
    # a fresh Config would use another.
    assert setting.default == _field_defaults()[setting.key]


@pytest.mark.parametrize("setting", settings.SETTINGS, ids=lambda s: s.key)
def test_setting_is_internally_coherent(setting):
    assert setting.section in dict(settings.SECTIONS), "unknown section"
    assert setting.label and setting.help, "every row needs a label and help text"
    if setting.kind == "choice":
        assert setting.choices, "a choice needs choices"
        assert setting.default in setting.choices
    if setting.default is None:
        assert setting.nullable, "a None default has to be a nullable setting"
        assert setting.example, "a None default has no printable form in the example file"


@pytest.mark.parametrize("section,_title", settings.SECTIONS)
def test_no_empty_sections(section, _title):
    assert settings.by_section(section), f"section {section!r} would render blank"


def test_example_file_matches_the_schema():
    # Regenerate with:
    #   python -c "from bgtracker.settings import example_toml; \
    #              open('config.example.toml','w').write(example_toml())"
    assert EXAMPLE_FILE.read_text() == settings.example_toml()


def test_example_file_parses_as_toml_once_uncommented():
    import tomllib

    body = "\n".join(
        line[2:] for line in settings.example_toml().splitlines()
        if line.startswith("# ") and " = " in line
    )
    parsed = tomllib.loads(body)
    # Every documented key is a real one, spelled the way the loader expects.
    assert set(parsed) == set(settings.BY_KEY)


# -- coercion ----------------------------------------------------------
def test_numeric_values_clamp_rather_than_raise():
    # A hand-edited absurd value must load as the bound, not stop the tracker.
    assert settings.BY_KEY["sim_count"].coerce(10**9) == 50000
    assert settings.BY_KEY["sim_count"].coerce(-5) == 500
    assert settings.BY_KEY["overlay_scale"].coerce(99.0) == 3.0


def test_int_settings_stay_ints():
    value = settings.BY_KEY["leaderboard_width_px"].coerce(120.7)
    assert isinstance(value, int) and value == 120


def test_skew_accepts_a_negative_offset():
    # The author's own calibration is -47; a min of 0 would silently break it.
    assert settings.BY_KEY["leaderboard_skew_px"].coerce(-47) == -47


def test_choice_rejects_an_unknown_value():
    with pytest.raises(ValueError):
        settings.BY_KEY["log_level"].coerce("chatty")


def test_none_is_rejected_unless_the_setting_is_nullable():
    assert settings.BY_KEY["overlay_scale"].coerce(None) is None
    with pytest.raises(ValueError):
        settings.BY_KEY["sim_count"].coerce(None)


def test_path_settings_expand_user():
    assert settings.BY_KEY["hearthstone_dir"].coerce("~/hs") == Path.home() / "hs"


def test_coerce_passes_unknown_keys_through():
    # pos_* panel positions are machine-written and deliberately schema-less.
    assert settings.coerce("pos_hud_x", 1864) == 1864


def test_defaults_config_matches_a_fresh_config():
    assert settings.defaults_config() == Config()


# -- sanitising a loaded config ----------------------------------------
def test_a_hand_edited_config_is_brought_inside_the_schema():
    """`load_config` assigns raw TOML, so this is the only thing that clamps it.

    Coercion used to be reachable only through the settings *window*, which
    meant an absurd hand-edited value loaded verbatim — `poll_active = 0` in
    particular would spin the tail loop.
    """
    cfg = settings.sanitize(Config(sim_count=10**9, poll_active=0.0, overlay_scale=99.0))
    assert cfg.sim_count == 50000
    assert cfg.poll_active == 0.05
    assert cfg.overlay_scale == 3.0


def test_a_value_the_schema_cannot_represent_falls_back_to_the_default():
    """Never raise on load. The tracker is normally launched by the game, where
    a traceback from a config typo goes nowhere anybody will ever read."""
    cfg = settings.sanitize(Config(log_level="chatty", sim_timeout=None))
    assert cfg.log_level == "info"
    assert cfg.sim_timeout == 6.0


def test_sanitize_leaves_a_default_config_untouched():
    assert settings.sanitize(Config()) == Config()


def test_sanitize_preserves_nullable_settings():
    # None is meaningful for these ("auto"); clamping it away would pin the
    # overlay to one scale and one monitor.
    cfg = settings.sanitize(Config())
    assert cfg.overlay_scale is None and cfg.overlay_monitor is None
    assert cfg.hearthstone_dir is None


def test_the_service_sanitizes_what_it_is_handed():
    service = settings.SettingsService(Config(sim_count=10**9), path=None)
    assert service.cfg.sim_count == 50000
