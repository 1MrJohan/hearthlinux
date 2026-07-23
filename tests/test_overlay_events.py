"""Event -> panel wiring in OverlayApp, against a recording stand-in window.

No display and no layer shell: OverlayApp is built with __new__ so only the
listener logic under test runs.
"""

from __future__ import annotations

import pytest

from bgtracker.parse import events as ev
from bgtracker.sim.client import SimResult
from bgtracker.state.game import BoardSnapshot, Minion, PlayerBoard

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
pytest.importorskip("gi.repository.Gtk4LayerShell")

from bgtracker.overlay.app import OverlayApp  # noqa: E402


class RecordingWindow:
    """Captures every set_*/clear_* call the app makes."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args))
        return record

    def last(self, name: str):
        for call, args in reversed(self.calls):
            if call == name:
                return args
        return None


def _board(hp: int = 20, tier: int = 3) -> PlayerBoard:
    return PlayerBoard(
        player_id=1, bg_player_id=1, hero_card_id="TB_BaconShop_HERO_34",
        hero_entity_id=1, health=hp, armor=0, tier=tier,
        minions=(Minion(entity_id=2, card_id="CS2_065", position=1, attack=1, health=3),),
    )


def _app() -> tuple[OverlayApp, RecordingWindow]:
    app = OverlayApp.__new__(OverlayApp)
    window = RecordingWindow()
    app.window = window
    app.pipeline = None
    app.standings = ()
    return app, window


ODDS = SimResult(won_percent=63, tied_percent=9, lost_percent=28,
                 avg_damage_won=14, avg_damage_lost=9)
SNAPSHOT = BoardSnapshot(turn=7, friendly=_board(18), opponent=_board(27, 5))


def test_phase_title_flips_across_a_whole_game():
    """Hero select -> combat -> recruit -> combat -> game over."""
    app, win = _app()
    seen = []

    app.on_event(ev.GameStart(), None)
    seen.append(win.last("set_phase")[0])
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    seen.append(win.last("set_phase")[0])
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.ShopReady(), None)
    seen.append(win.last("set_phase")[0])
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    seen.append(win.last("set_phase")[0])
    app.on_event(ev.GameEnd(placement=1), None)
    seen.append(win.last("set_phase")[0])

    assert seen == [
        "Hero Select", "Combat Forecast", "Recruit Phase",
        "Combat Forecast", "Game Over",
    ]


def test_combat_display_survives_the_engine_resolving_the_fight():
    """CombatEnd fires ~1s in; the player watches for 20-45s more.

    Acting on it flips the HUD to Recruit Phase and collapses the odds while
    the battle is still animating on screen.
    """
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    assert win.last("set_phase")[0] == "Combat Forecast"
    assert win.last("set_forecast_live") is None, "forecast collapsed too early"
    assert win.last("clear_board") is None, "enemy board cleared mid-animation"


def test_shop_ready_ends_the_combat_display():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_phase") == ("Recruit Phase", "18 HP · Tavern 3")
    assert win.last("set_forecast_live") == (False,)
    assert win.last("set_combat") == (False,)


def test_shop_ready_outside_combat_is_ignored():
    """The marker also fires around hero select; it must not clobber the phase."""
    app, win = _app()
    app.on_event(ev.GameStart(), None)
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_phase")[0] == "Hero Select"


def test_phase_meta_reports_your_hp_not_the_opponents():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    assert win.last("set_phase")[1].startswith("18 HP")   # friendly board
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_phase")[1] == "18 HP · Tavern 3"


def test_medallion_pulses_for_the_whole_visible_fight():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    assert win.last("set_combat") == (True,)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    assert win.last("set_combat") == (True,), "stopped pulsing mid-animation"
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_combat") == (False,)


def test_turn_number_tracks_turn_changes():
    app, win = _app()
    app.on_event(ev.TurnChange(turn=7), None)
    assert win.last("set_turn") == (7,)
    app.on_event(ev.GameStart(), None)
    assert win.last("set_turn") == (None,)


def test_combat_start_shows_the_forecast():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    assert win.last("set_odds") == (63, 9, 28)
    assert win.last("set_damage") == (14, 9)


def test_odds_survive_the_end_of_combat():
    """The forecast is the reason the overlay exists.

    Combat resolves in seconds and the sim is awaited before the overlay is
    even told about it, so clearing on CombatEnd leaves almost no window to
    read the numbers — and none at all afterwards, when you are shopping and
    actually want to know how the fight went.
    """
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    assert win.last("set_odds") == (63, 9, 28), "odds were cleared when combat ended"
    assert win.last("set_damage") == (14, 9), "damage forecast was cleared too"


def test_a_new_combat_replaces_the_previous_forecast():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    later = SimResult(won_percent=10, tied_percent=5, lost_percent=85,
                      avg_damage_won=2, avg_damage_lost=20)
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), later)
    assert win.last("set_odds") == (10, 5, 85)


def test_combat_without_a_prediction_clears_stale_odds():
    """A board the mapper can't handle must not leave the last fight's numbers up."""
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), None)
    assert win.last("set_odds") == (None, None, None)


def test_new_game_clears_the_forecast():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.GameStart(), None)
    assert win.last("set_odds") == (None, None, None)


def test_game_end_clears_the_forecast():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.GameEnd(placement=3), None)
    assert win.last("set_odds") == (None, None, None)
