"""The overlay's state model and its diffing renderer.

Two properties matter here and nothing else really does:

* A rebuilt window comes back showing what the old one showed. Changing
  `overlay_scale` destroys and rebuilds the overlay, and before there was a
  state model that left a blank HUD until the next event happened to arrive —
  which mid-game can be half a minute.
* `render` emits only what changed. Not calling a setter is behaviour, not an
  optimisation: it is what keeps the combat result banked until `ShopReady`
  instead of revealed mid-fight.
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
from bgtracker.overlay.model import BoardView, OverlayState, render  # noqa: E402


class RecordingWindow:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args))
        return record

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

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
    app.window = RecordingWindow()
    app.pipeline = None
    app.reset_state()
    return app, app.window


ODDS = SimResult(won_percent=63, tied_percent=9, lost_percent=28,
                 avg_damage_won=14, avg_damage_lost=9, lost_lethal_percent=12.0)
SNAPSHOT = BoardSnapshot(turn=7, friendly=_board(18), opponent=_board(27, 5))


# -- diffing -----------------------------------------------------------
def test_render_emits_everything_when_the_window_is_new():
    win = RecordingWindow()
    render(win, OverlayState(), previous=None)
    # The rebuild path: nothing is on screen, so every setter has to fire.
    for name in (
        "set_phase", "set_status", "set_turn", "set_combat", "set_odds",
        "set_damage", "set_lethal", "set_result", "set_forecast_live",
        "set_next_forecast", "set_standings", "set_buffs", "set_hot_place",
    ):
        assert name in win.names(), f"{name} was never pushed to a fresh window"


def test_render_emits_nothing_when_nothing_changed():
    win = RecordingWindow()
    state = OverlayState()
    render(win, state, previous=OverlayState())
    assert win.calls == []


def test_render_emits_only_the_field_that_moved():
    win = RecordingWindow()
    before = OverlayState()
    state = before.snapshot()
    state.turn = 7
    render(win, state, previous=before)
    assert win.names() == ["set_turn"]
    assert win.last("set_turn") == (7,)


def test_a_cleared_board_calls_clear_not_set():
    win = RecordingWindow()
    before = OverlayState(board=BoardView("Enemy Board", "x", None))
    state = before.snapshot()
    state.board = None
    render(win, state, previous=before)
    assert win.names() == ["clear_board"]


def test_odds_are_pushed_before_damage():
    # The window clears damage and lethal from inside set_odds when the win
    # figure is None, so the reverse order would wipe values set moments before.
    win = RecordingWindow()
    state = OverlayState(odds=(63, 9, 28), damage=("14", "9"))
    render(win, state, previous=None)
    assert win.names().index("set_odds") < win.names().index("set_damage")


def test_snapshot_is_a_copy_not_an_alias():
    state = OverlayState()
    taken = state.snapshot()
    state.turn = 9
    assert taken.turn is None, "a live alias would make every diff come up empty"


# -- state after real event sequences ----------------------------------
def test_state_tracks_a_combat():
    app, _ = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    st = app.state
    # The opponent name comes from the card DB, which tests do not download.
    assert st.phase[0] == "Combat Forecast"
    assert st.phase[1].startswith("18 HP · vs ")
    assert st.combat is True
    assert st.turn == 7
    assert st.odds == (63, 9, 28)
    assert st.damage == ("14", "9")
    assert st.lethal == 12.0
    # The game is showing this fight itself; the panel would only cover it.
    # Reviewing a player is the scout popout's job (hover_board).
    assert st.board is None


def test_shop_buffs_reach_the_panel_alongside_the_played_buffs():
    """Two different quantities: what a minion gains when played, and what one
    in Bob's tavern already carries. The panel shows both, separately."""
    app, win = _app()
    app.on_event(
        ev.Buffs(entries=(("Blood Gem", 2, 2),), shop=(("Elemental", 27, 27),)),
        None,
    )
    assert win.last("set_buffs") == (
        (("Blood Gem", 2, 2),), (("Elemental", 27, 27),), 0, 0,
    )


def test_gold_and_rerolls_reach_the_panel():
    app, win = _app()
    app.on_event(ev.Buffs(entries=(), gold_next_turn=2, free_rerolls=1), None)
    assert win.last("set_buffs") == ((), (), 2, 1)


def test_the_banked_result_is_not_in_state_until_shop_ready():
    app, _ = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatResult(turn=7, outcome="win", damage=14), None)
    # It is on the app, deliberately not on the display state — the player is
    # still watching the fight it gives away.
    assert app.state.result == (None, 0)
    assert app._shop_result == ("win", 14)

    app.on_event(ev.ShopReady(), None)
    assert app.state.result == ("win", 14)


# -- the rebuild path --------------------------------------------------
def test_a_rebuilt_window_is_repainted_from_state():
    """This is the whole reason the state model exists.

    Changing overlay_scale destroys the window and builds a new one. Without a
    state model the replacement comes up empty and stays empty until the next
    event — which, mid recruit phase, can be thirty seconds of blank HUD.
    """
    app, first = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.Standings(places=()), None)

    # What OverlayApp._build_window does after swapping the window out.
    replacement = RecordingWindow()
    app.window = replacement
    app._rendered = None
    app._flush()

    assert replacement.last("set_phase")[0] == "Combat Forecast"
    assert replacement.last("set_phase")[1].startswith("18 HP · vs ")
    assert replacement.last("set_odds") == (63, 9, 28)
    assert replacement.last("set_damage") == ("14", "9")
    assert replacement.last("set_combat") == (True,)
    assert replacement.last("set_turn") == (7,)
    assert "set_board" not in replacement.names()


def test_a_rebuild_mid_recruit_phase_keeps_the_result_on_screen():
    app, _ = _app()
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.CombatEnd(snapshot=SNAPSHOT), None)
    app.on_event(ev.CombatResult(turn=7, outcome="loss", damage=9), None)
    app.on_event(ev.ShopReady(), None)

    replacement = RecordingWindow()
    app.window = replacement
    app._rendered = None
    app._flush()

    assert replacement.last("set_result") == ("loss", 9)
    assert replacement.last("set_odds") == (63, 9, 28)
    assert replacement.last("set_phase")[0] == "Recruit Phase"
    assert replacement.last("clear_board") == ()


def test_events_arriving_before_the_window_exists_are_not_lost():
    """Startup replays the whole session log before the window is up."""
    app, _ = _app()
    app.window = None
    app.on_event(ev.CombatStart(snapshot=SNAPSHOT), ODDS)
    app.on_event(ev.TurnChange(turn=9), None)

    late = RecordingWindow()
    app.window = late
    app._rendered = None
    app._flush()
    assert late.last("set_turn") == (9,)
    assert late.last("set_odds") == (63, 9, 28)
