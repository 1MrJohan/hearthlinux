"""Event -> panel wiring in OverlayApp, against a recording stand-in window.

No display and no layer shell: OverlayApp is built with __new__ so only the
listener logic under test runs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bgtracker.parse import events as ev
from bgtracker.sim.client import SimResult
from bgtracker.state.game import BoardSnapshot, Minion, PlayerBoard
from bgtracker.state.opponents import OpponentMemory

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


def _board(hp: int = 20, tier: int = 3, player_id: int = 1) -> PlayerBoard:
    return PlayerBoard(
        player_id=player_id, bg_player_id=player_id,
        hero_card_id="TB_BaconShop_HERO_34",
        hero_entity_id=player_id, health=hp, armor=0, tier=tier,
        minions=(Minion(entity_id=2, card_id="CS2_065", position=1, attack=1, health=3),),
    )


def _app(mmr_prompt: bool = True) -> tuple[OverlayApp, RecordingWindow]:
    from bgtracker.config import Config

    app = OverlayApp.__new__(OverlayApp)
    window = RecordingWindow()
    app.window = window
    app.pipeline = None
    # The end-of-game handler consults the live Config, which the real
    # SettingsService owns and hands to everything downstream unchanged.
    app.settings = SimpleNamespace(cfg=Config(mmr_prompt=mmr_prompt))
    # What the real constructor does: an empty display, and a record of what
    # the window has been told so far so `render` can emit only differences.
    app.reset_state()
    return app, window


ODDS = SimResult(won_percent=63, tied_percent=9, lost_percent=28,
                 avg_damage_won=14, avg_damage_lost=9)
SNAPSHOT = BoardSnapshot(turn=7, friendly=_board(18), opponent=_board(27, 5))


def _prepare_opponent_hover(app: OverlayApp) -> PlayerBoard:
    board = _board(27, 5, player_id=4)
    memory = OpponentMemory()
    memory.record(6, board)
    app.pipeline = SimpleNamespace(memory=memory)
    app.on_event(ev.Standings(places=(
        ev.Standing(
            place=1, player_id=4, hero_card_id="TB_BaconShop_HERO_34",
            health=27,
        ),
        ev.Standing(
            place=2, player_id=5, hero_card_id="TB_BaconShop_HERO_52",
            health=24,
        ),
    )), None)
    app.on_event(ev.NextOpponent(player_id=4), None)
    return board


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
    board = _prepare_opponent_hover(app)
    app._on_hover_slot(0)
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=6, turn=8), ODDS)
    shown = win.last("set_hover_board")
    assert shown[2] == board
    assert "next opponent" in shown[1]
    text = shown[5]
    assert "63" in text and "28" in text
    assert "2 turns old" in text


def test_a_board_seen_this_turn_is_not_called_stale():
    app, win = _app()
    _prepare_opponent_hover(app)
    app._on_hover_slot(0)
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=8, turn=8), ODDS)
    assert "current" in win.last("set_hover_board")[5]


def test_next_opponent_stays_hidden_until_hovered():
    app, win = _app()
    _prepare_opponent_hover(app)
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=6, turn=8), ODDS)
    assert app.state.next_forecast is not None
    assert win.last("set_hover_board") is None


def test_another_opponents_hover_never_shows_the_next_forecast():
    app, win = _app()
    _prepare_opponent_hover(app)
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=6, turn=8), ODDS)
    app._on_hover_slot(1)
    shown = win.last("set_hover_board")
    assert "next opponent" not in shown[1]
    assert shown[5] is None


def test_leaving_an_opponent_clears_the_hover_popout():
    app, win = _app()
    _prepare_opponent_hover(app)
    app._on_hover_slot(0)
    assert win.last("set_hover_board") is not None
    app._on_hover_slot(None)
    assert win.last("clear_hover_board") == ()


def test_a_new_opponent_drops_the_previous_forecast():
    """Odds for the player you are no longer facing are worse than none."""
    app, win = _app()
    _prepare_opponent_hover(app)
    app._on_hover_slot(0)
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=6, turn=8), ODDS)
    assert win.last("set_hover_board")[5] is not None
    app.on_event(ev.NextOpponent(player_id=5), None)
    assert app.state.next_forecast is None
    shown = win.last("set_hover_board")
    assert "next opponent" not in shown[1]
    assert shown[5] is None


def test_an_unavailable_shop_forecast_replaces_stale_odds():
    app, win = _app()
    _prepare_opponent_hover(app)
    app._on_hover_slot(0)
    event = ev.ShopForecast(opponent_id=4, seen_turn=6, turn=8)
    app.on_event(event, ODDS)
    app.on_event(event, None)
    assert win.last("set_hover_board")[5] == "Odds unavailable for this board"


def test_a_friendly_board_change_hides_odds_while_they_recalculate():
    app, win = _app()
    _prepare_opponent_hover(app)
    app._on_hover_slot(0)
    app.on_event(ev.ShopForecast(opponent_id=4, seen_turn=6, turn=8), ODDS)
    app.on_event(ev.ShopBoard(board=SNAPSHOT.friendly, turn=8), None)
    assert win.last("set_hover_board")[5] is None


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


def test_medallion_keeps_its_combat_emphasis_for_the_whole_visible_fight():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    assert win.last("set_combat") == (True,)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    assert win.last("set_combat") == (True,), "lost combat emphasis mid-animation"
    app.on_event(ev.ShopReady(), None)
    assert win.last("set_combat") == (False,)


def test_turn_number_tracks_turn_changes():
    app, win = _app()
    app.on_event(ev.TurnChange(turn=7), None)
    assert win.last("set_turn") == (7,)
    app.on_event(ev.GameStart(), None)
    assert win.last("set_turn") == (None,)


def test_visible_combat_keeps_its_turn_until_the_animation_finishes():
    """GameState advances almost immediately, while PowerTaskList keeps the
    player in combat for another 20-45 seconds."""
    app, win = _app()
    app.on_event(ev.TurnChange(turn=7), None)
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)

    # The engine has resolved combat 7 and entered its internal turn 8, but
    # the player is still watching combat 7 and its forecast.
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.TurnChange(turn=8), None)
    assert win.last("set_turn") == (7,)
    assert app.state.turn == 7

    app.on_event(ev.ShopReady(), None)
    assert win.last("set_turn") == (8,)
    assert app.state.turn == 8


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
    assert win.last("set_status") == ("Odds unavailable for this combat",)


def test_a_later_supported_combat_clears_the_unavailable_message():
    app, win = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    assert win.last("set_status") == ("",)


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


# -- the end-of-game MMR nudge -----------------------------------------
# Ratings are the one thing the log never carries, so the readings are only
# ever as complete as the player remembers to type in. The nudge asks at the
# one moment the number is on screen — and because it is a hole in the
# overlay's click-through guarantee for as long as it is up, when it comes
# down matters as much as when it goes up.
def test_a_finished_game_asks_for_a_rating():
    app, win = _app()
    app.on_event(ev.GameEnd(placement=3), None)
    assert win.last("set_mmr_prompt") == (True,)


def test_a_game_that_ended_without_a_placement_still_asks():
    """`finalize()` flushes a GameEnd carrying None when the placement tags
    never arrived. The game is still over, and the rating still moved."""
    app, win = _app()
    app.on_event(ev.GameEnd(placement=None), None)
    assert win.last("set_mmr_prompt") == (True,)


def test_the_next_game_takes_the_nudge_down():
    """Also what keeps catch-up quiet: a replayed GameEnd raises it, and every
    catch-up game but the last is followed by a GameStart."""
    app, win = _app()
    app.on_event(ev.GameEnd(placement=3), None)
    app.on_event(ev.GameStart(), None)
    assert win.last("set_mmr_prompt") == (False,)


def test_the_nudge_times_out():
    app, win = _app()
    app.on_event(ev.GameEnd(placement=3), None)
    app._mmr_timed_out()
    assert win.last("set_mmr_prompt") == (False,)
    assert app.state.mmr_prompt is False


def test_the_setting_suppresses_the_nudge_entirely():
    app, win = _app(mmr_prompt=False)
    app.on_event(ev.GameEnd(placement=3), None)
    assert win.last("set_mmr_prompt") is None
    assert app.state.mmr_prompt is False


def test_turning_the_setting_off_takes_a_visible_nudge_down():
    """Every setting applies live; this one would otherwise wait for a game."""
    app, win = _app()
    app.on_event(ev.GameEnd(placement=3), None)
    app.settings.cfg.mmr_prompt = False
    app._apply_overlay({"mmr_prompt"})
    assert win.last("set_mmr_prompt") == (False,)
