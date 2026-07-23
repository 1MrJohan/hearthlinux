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


def test_bundled_font_files_exist():
    missing = [n for n in theme.FONT_FILES if not (theme.FONT_DIR / n).is_file()]
    assert missing == [], f"bundled fonts missing: {missing}"


def test_px_never_collapses_a_visible_edge():
    # A 1px hairline scaled to 0.5 must stay visible, not round away to 0.
    assert theme.px(1, 0.5) == 1
    assert theme.px(1, 0.1) == 1
    assert theme.px(12, 2.0) == 24


def test_pip_colours_exclude_golden():
    # "G" is the tile border treatment, not a keyword pip.
    assert "G" not in theme.PIP_COLOURS


def test_every_pip_and_buff_colour_reaches_the_stylesheet():
    css = theme.stylesheet(1.0)
    for letter in theme.PIP_COLOURS:
        assert f".pip-{letter.lower()}" in css
    for label in theme.BUFF_COLOURS:
        assert f".buff-{label.lower().replace(' ', '')}" in css
