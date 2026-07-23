"""HudPanel forecast visibility: live, collapsed, and recalled on hover."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from bgtracker.overlay import theme  # noqa: E402
from bgtracker.overlay.hud import HudPanel  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def gtk():
    theme.register_fonts()
    Gtk.init()


@pytest.fixture
def hud():
    panel = HudPanel(1.0)
    panel.set_turn(7)
    return panel


def _state(panel) -> tuple[bool, bool, bool]:
    """(odds shown, damage pills shown, collapsed hint shown)"""
    return (panel.odds_box.get_visible(), panel.pills.get_visible(),
            panel.hint.get_visible())


def test_no_forecast_shows_nothing(hud):
    hud.set_odds(None, None, None)
    assert _state(hud) == (False, False, False)


def test_live_combat_shows_the_forecast(hud):
    hud.set_odds(63, 9, 28)
    hud.set_damage(14, 9)
    assert _state(hud) == (True, True, False)


def test_combat_end_collapses_to_the_hint(hud):
    hud.set_odds(63, 9, 28)
    hud.set_damage(14, 9)
    hud.set_forecast_live(False)
    assert _state(hud) == (False, False, True)


def test_hover_recalls_the_collapsed_forecast(hud):
    hud.set_odds(63, 9, 28)
    hud.set_damage(14, 9)
    hud.set_forecast_live(False)
    hud.set_hovered(True)
    assert _state(hud) == (True, True, False)
    hud.set_hovered(False)
    assert _state(hud) == (False, False, True)


def test_hover_with_no_forecast_shows_nothing(hud):
    hud.set_odds(None, None, None)
    hud.set_hovered(True)
    assert _state(hud) == (False, False, False)


def test_a_new_forecast_expands_again_even_while_unhovered(hud):
    hud.set_odds(63, 9, 28)
    hud.set_forecast_live(False)
    assert _state(hud)[2] is True
    hud.set_odds(10, 5, 85)          # next combat starts
    assert _state(hud)[0] is True


def test_clearing_the_forecast_drops_the_hint_too(hud):
    hud.set_odds(63, 9, 28)
    hud.set_damage(14, 9)
    hud.set_forecast_live(False)
    hud.set_odds(None, None, None)   # new game
    assert _state(hud) == (False, False, False)


def test_bar_segments_span_the_track(hud):
    hud.set_odds(63, 9, 28)
    total = sum(hud.bar_segments[k].get_size_request().width
                for k in ("win", "tie", "loss"))
    assert total == hud.track
