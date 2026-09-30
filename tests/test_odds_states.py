"""Combat odds states: the simulating signal and the named reason for none.

See docs/superpowers/specs/2026-09-30-odds-states-design.md.
"""

from __future__ import annotations

import asyncio

from bgtracker.app import Pipeline, describe_blocker, describe_failure
from bgtracker.parse import events as ev
from bgtracker.sim.client import UnsupportedCombatCardsError
from bgtracker.state.game import BoardSnapshot

from .test_shop_forecast import StubSim, _board

SNAP = BoardSnapshot(turn=7, friendly=_board(1), opponent=_board(4))


class RaisingSim(StubSim):
    def __init__(self, exc: Exception):
        super().__init__()
        self.exc = exc

    async def simulate(self, battle_info, on_partial=None, sims=None, background=False):
        raise self.exc


def _run(sim, snapshot=SNAP, historical=False) -> list:
    pipe = Pipeline(sim=sim, db=None)
    seen: list = []
    pipe.listeners.append(lambda e, p: seen.append((type(e).__name__, e, p)))
    asyncio.run(pipe.handle([ev.CombatStart(snapshot=snapshot)], historical=historical))
    return seen


def test_simulating_is_announced_before_the_result():
    names = [name for name, _, _ in _run(StubSim())]
    assert names == ["CombatSimulating", "CombatStart"]


def test_a_forecast_carries_no_unavailable_event():
    assert "OddsUnavailable" not in [name for name, _, _ in _run(StubSim())]


def _reason(seen) -> str:
    [event] = [e for name, e, _ in seen if name == "OddsUnavailable"]
    return event.reason


def test_catch_up_says_the_tracker_started_mid_fight():
    seen = _run(StubSim(), historical=True)
    assert "CombatSimulating" not in [name for name, _, _ in seen]
    assert _reason(seen) == "tracker started mid-fight"


def test_no_simulator_says_so():
    assert _reason(_run(None)) == "simulator not running"


def test_a_blocked_board_is_not_announced_as_simulating():
    blocked = BoardSnapshot(turn=7, friendly=_board(1), opponent=None)
    seen = _run(StubSim(), snapshot=blocked)
    assert "CombatSimulating" not in [name for name, _, _ in seen]
    assert _reason(seen) == "a board isn't fully visible"


def test_a_failed_run_names_the_failure():
    assert _reason(_run(RaisingSim(TimeoutError()))) == "simulator timed out"
    assert _reason(_run(RaisingSim(RuntimeError("sidecar died mid-request")))) == "simulator crashed"


def test_unsupported_cards_are_named_two_at_most():
    exc = UnsupportedCombatCardsError("x", ["A_1", "B_2", "C_3"])
    assert describe_failure(exc).startswith("simulator can't model ")
    assert describe_failure(exc).endswith("…")


def test_every_blocker_gets_a_player_facing_reason(monkeypatch):
    """The blocker strings are pinned by the mapper; each must map, not leak."""
    monkeypatch.setattr("bgtracker.app.cards.name", lambda card_id: "Some Power")
    for blocker in (
        "combat hero power not modeled: TB_BaconShop_HP_103",
        "hero power state not recorded: TB_BaconShop_HP_037a",
        "combat secret identity hidden",
        "deity secret incomplete",
        "board incomplete",
    ):
        text = describe_blocker(blocker)
        assert text != blocker and ":" not in text and "_" not in text, text
