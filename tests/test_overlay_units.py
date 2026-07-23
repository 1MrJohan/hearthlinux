"""Pure logic behind the overlay's rendering — no widgets, no display."""

from __future__ import annotations

import pytest

from bgtracker.state.game import Minion

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from bgtracker.overlay.hud import bar_widths  # noqa: E402
from bgtracker.overlay.widgets import pip_classes  # noqa: E402


# -- odds bar ----------------------------------------------------------
@pytest.mark.parametrize(
    "odds",
    [(63, 9, 28), (100, 0, 0), (0, 0, 100), (33, 33, 34), (1, 98, 1), (50, 0, 50)],
)
def test_bar_segments_always_fill_the_track(odds):
    track = 274
    widths = bar_widths(*odds, track)
    assert sum(widths) == track, "a rounding gap would leave a notch in the bar"
    assert all(w >= 0 for w in widths)


def test_bar_widths_are_proportional():
    win, tie, loss = bar_widths(50, 25, 25, 200)
    assert (win, tie, loss) == (100, 50, 50)


def test_bar_widths_hide_zero_shares():
    win, tie, loss = bar_widths(100, 0, 0, 274)
    assert (win, tie, loss) == (274, 0, 0)


@pytest.mark.parametrize("track", [0, -5])
def test_bar_widths_tolerate_an_unrealised_track(track):
    # Before the panel is measured the track can be 0; must not divide by it.
    assert bar_widths(63, 9, 28, track) == (0, 0, 0)


def test_bar_widths_tolerate_absent_odds():
    assert bar_widths(0, 0, 0, 274) == (0, 0, 0)


# -- keyword pips ------------------------------------------------------
def _minion(**flags) -> Minion:
    return Minion(entity_id=1, card_id="X", position=1, attack=1, health=1, **flags)


def test_pips_follow_flag_order():
    minion = _minion(taunt=True, divine_shield=True, reborn=True)
    assert pip_classes(minion.flags) == ["pip-t", "pip-d", "pip-r"]


def test_golden_is_not_a_pip():
    # Golden is the tile's border treatment; a pip would double up on it.
    minion = _minion(golden=True, taunt=True)
    assert "G" in minion.flags
    assert pip_classes(minion.flags) == ["pip-t"]


def test_plain_minion_has_no_pips():
    assert pip_classes(_minion().flags) == []


def test_unknown_letters_are_ignored():
    assert pip_classes("TQZD") == ["pip-t", "pip-d"]


def test_windfury_variants_collapse_to_one_pip():
    assert pip_classes(_minion(windfury=True, mega_windfury=True).flags) == ["pip-w"]
