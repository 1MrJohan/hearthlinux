"""The Dark Oak stylesheet must parse without a single GTK CSS warning.

GTK does not raise on a bad declaration — it emits a parsing-error signal and
silently drops the rule, so a typo shows up as "that panel looks unstyled"
rather than a traceback. These tests turn that into a hard failure.

No display is needed: Gtk.CssProvider parses standalone.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from bgtracker.overlay import theme  # noqa: E402

SCALES = [0.5, 1.0, 1.5, 3.0]


def parse_errors(css: str) -> list[str]:
    errors: list[str] = []
    provider = Gtk.CssProvider()
    provider.connect(
        "parsing-error", lambda _p, _section, error: errors.append(error.message)
    )
    provider.load_from_string(css)
    return errors


@pytest.mark.parametrize("scale", SCALES)
def test_stylesheet_parses_clean(scale):
    assert parse_errors(theme.stylesheet(scale)) == []


@pytest.mark.parametrize("scale", SCALES)
def test_no_unsubstituted_placeholders(scale):
    # string.Template leaves "$name" in place only if substitute() was bypassed;
    # a stray "$" means a token was renamed without updating the CSS.
    assert "$" not in theme.stylesheet(scale)


def test_settings_stylesheet_parses_clean():
    assert parse_errors(theme.settings_stylesheet()) == []


def test_settings_stylesheet_has_no_unsubstituted_placeholders():
    assert "$" not in theme.settings_stylesheet()


@pytest.mark.parametrize("scale", SCALES)
def test_transparency_is_scoped_to_the_overlays_own_windows(scale):
    """A bare `window {}` rule here would make the settings window unusable.

    These providers attach to the whole display, not to one window, so an
    unscoped background rule reaches every window the process opens — which is
    how the settings window would come up transparent over a light system
    theme. Both stylesheets must qualify the selector.
    """
    for css in (theme.stylesheet(scale), theme.settings_stylesheet()):
        for line in css.splitlines():
            selector = line.split("{")[0].strip()
            assert selector != "window", f"unscoped window rule: {line.strip()!r}"


def test_the_two_stylesheets_do_not_style_each_others_windows():
    assert "window.bg-overlay" in theme.stylesheet(1.0)
    assert "bg-overlay" not in theme.settings_stylesheet()
    assert "bg-settings" not in theme.stylesheet(1.0)


def test_bundled_font_files_exist():
    missing = [n for n in theme.FONT_FILES if not (theme.FONT_DIR / n).is_file()]
    assert missing == [], f"bundled fonts missing: {missing}"


def test_px_never_collapses_a_visible_edge():
    # A 1px hairline scaled to 0.5 must stay visible, not round away to 0.
    assert theme.px(1, 0.5) == 1
    assert theme.px(1, 0.1) == 1
    assert theme.px(12, 2.0) == 24


@pytest.mark.parametrize(
    "monitor_height,expected",
    [(1080, 1.53), (1440, 2.04), (2160, 3.0), (720, 1.02)],
)
def test_auto_scale_matches_the_mock_proportions(monitor_height, expected):
    assert theme.auto_scale(monitor_height) == expected


@pytest.mark.parametrize("monitor_height", [0, -1, 10, 100_000])
def test_auto_scale_survives_nonsense_geometry(monitor_height):
    # A missing monitor reports 0; clamping keeps the overlay renderable.
    assert theme.MIN_SCALE <= theme.auto_scale(monitor_height) <= theme.MAX_SCALE


def test_stylesheet_parses_at_the_auto_scale_for_common_monitors():
    for height in (720, 1080, 1440, 2160):
        assert parse_errors(theme.stylesheet(theme.auto_scale(height))) == []


def test_pip_colours_exclude_golden():
    # "G" is the tile border treatment, not a keyword pip.
    assert "G" not in theme.PIP_COLOURS


def test_every_pip_and_buff_colour_reaches_the_stylesheet():
    css = theme.stylesheet(1.0)
    for letter in theme.PIP_COLOURS:
        assert f".pip-{letter.lower()}" in css
    for label in theme.BUFF_COLOURS:
        assert f".buff-{label.lower().replace(' ', '')}" in css
