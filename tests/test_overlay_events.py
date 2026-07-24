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
    # What the real constructor does: an empty display, and a record of what
    # the window has been told so far so `render` can emit only differences.
    app.reset_state()
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


def test_a_provisional_forecast_shows_before_the_run_finishes():
    """A heavy 7v7 board takes seconds to simulate; the odds block should not
    sit empty until then."""
    app, win = _app()
    rough = SimResult(won_percent=60, tied_percent=10, lost_percent=30,
                      avg_damage_won=13, avg_damage_lost=8, sims_run=400)
    app.on_event(ev.CombatForecast(snapshot=SNAPSHOT), rough)
    assert win.last("set_odds") == (60, 10, 30)
    assert win.last("set_phase")[0] == "Combat Forecast"

    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    assert win.last("set_odds") == (63, 9, 28), "final numbers did not replace the partial"


def test_a_forecast_without_numbers_is_ignored():
    """The guard is on the prediction, not the event: a partial with nothing in
    it must not blank a forecast already on screen."""
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatForecast(snapshot=SNAPSHOT), None)
    assert win.last("set_odds") == (63, 9, 28)


def test_the_result_waits_for_the_animation_to_finish():
    """CombatResult arrives ~1s into a fight the player watches for 20-45s.

    Showing it the moment it lands spoils the battle they are still watching,
    so it is banked and revealed at ShopReady — the same rule the phase title
    already follows.
    """
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatResult(turn=7, outcome="win", damage=14), None)
    assert win.last("set_result") is None, "result revealed mid-animation"

    app.on_event(ev.ShopReady(), None)
    assert win.last("set_result") == ("win", 14)


def test_the_forecast_stays_open_through_the_recruit_phase():
    """The forecast only becomes checkable once the fight is over, so that is
    the worst possible moment to hide it behind a hover."""
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatResult(turn=7, outcome="loss", damage=9), None)
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_odds") == (63, 9, 28)
    assert win.last("set_result") == ("loss", 9)


def test_a_repeated_result_is_shown_again_for_the_next_fight():
    """Two combats in a row with the same (outcome, damage) — back-to-back ties
    are common — must each show their result caption.

    The renderer skips a setter whose value has not changed, and set_odds hides
    the result widget when the next fight's forecast arrives, so the second
    identical result has to be pushed afresh or it silently never reappears.
    """
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatResult(turn=7, outcome="tie", damage=0), None)
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_result") == ("tie", 0)

    # A second fight: a different forecast (so set_odds fires and clears the
    # result widget), but the identical outcome.
    other = SimResult(won_percent=20, tied_percent=60, lost_percent=20,
                      avg_damage_won=0, avg_damage_lost=0)
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), other)
    mark = len(win.calls)   # everything past here belongs to the 2nd fight's end
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatResult(turn=7, outcome="tie", damage=0), None)
    app.on_event(ev.ShopReady(), None)
    assert ("set_result", ("tie", 0)) in win.calls[mark:], \
        "the second identical result was never re-shown"


def test_a_result_from_a_fight_with_no_forecast_is_still_shown():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatResult(turn=7, outcome="tie", damage=0), None)
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_result") == ("tie", 0)


def test_a_new_game_clears_the_previous_result():
    app, win = _app()
    # The full sequence, so the result genuinely reaches the screen: ShopReady
    # only reveals it when a combat was actually being watched.
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatResult(turn=7, outcome="win", damage=14), None)
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_result") == ("win", 14)

    app.on_event(ev.GameStart(), None)
    assert win.last("set_result") == (None, 0)


def test_a_result_that_never_reached_the_screen_needs_no_clearing():
    """A CombatResult with no combat behind it is banked and dropped.

    ShopReady is gated on having been in combat, so this one never becomes
    visible — and a new game therefore has nothing to clear. Asserted because
    the overlay only pushes values that changed, which makes "no call" a
    meaningful outcome rather than an accident.
    """
    app, win = _app()
    app.on_event(ev.CombatResult(turn=7, outcome="win", damage=14), None)
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_result") is None, "revealed a result outside combat"
    app.on_event(ev.GameStart(), None)
    assert win.last("set_result") is None


def test_the_shop_forecast_says_how_stale_its_board_is():
    """The opponent keeps buying after you last saw them, so the number is a
    guess and has to admit it."""
    app, win = _app()
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=6, turn=8), ODDS)
    text = win.last("set_next_forecast")[0]
    assert "63" in text and "28" in text
    assert "2 turns old" in text


def test_a_board_seen_this_turn_is_not_called_stale():
    app, win = _app()
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=8, turn=8), ODDS)
    assert "current" in win.last("set_next_forecast")[0]


def test_a_new_opponent_drops_the_previous_forecast():
    """Odds for the player you are no longer facing are worse than none."""
    app, win = _app()
    app.pipeline = None
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=6, turn=8), ODDS)
    assert win.last("set_next_forecast")[0] is not None
    app.on_event(ev.NextOpponent(player_id=5), None)
    assert win.last("set_next_forecast") == (None,)


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
    assert win.last("set_damage") == ("14", "9")


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
    assert win.last("set_damage") == ("14", "9"), "damage forecast was cleared too"


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
