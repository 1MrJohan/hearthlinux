"""Shop-phase forecast loop: debouncing, staleness, and yielding to combat."""

from __future__ import annotations

import asyncio

from bgtracker.app import SHOP_DEBOUNCE, Pipeline
from bgtracker.parse import events as ev
from bgtracker.sim.client import SimResult
from bgtracker.state.game import BoardSnapshot, Minion, PlayerBoard


class StubSim:
    """Records every simulate() call instead of running one."""

    def __init__(self):
        self.calls: list[int] = []
        self.lanes: list[bool] = []

    async def simulate(self, battle_info, on_partial=None, sims=None, background=False):
        self.calls.append(sims)
        self.lanes.append(background)
        return SimResult(
            won_percent=55, tied_percent=5, lost_percent=40,
            avg_damage_won=10, avg_damage_lost=8, sims_run=sims or 8000,
        )


def _board(pid: int, minions: int = 1) -> PlayerBoard:
    return PlayerBoard(
        player_id=pid, bg_player_id=pid, hero_card_id="TB_BaconShop_HERO_11",
        hero_entity_id=pid, health=30, armor=0, tier=3,
        minions=tuple(
            Minion(entity_id=100 + i, card_id="BG_EX1_506", position=i + 1, attack=2, health=3)
            for i in range(minions)
        ),
    )


def _pipeline() -> tuple[Pipeline, StubSim, list]:
    sim = StubSim()
    pipe = Pipeline(sim=sim, db=None)
    seen: list = []
    pipe.listeners.append(lambda e, p: seen.append((e, p)) if isinstance(e, ev.ShopForecast) else None)
    # Scout the opponent, as fighting them once would.
    pipe.memory.record(6, _board(4, minions=2))
    return pipe, sim, seen


async def _drain(pipe: Pipeline) -> None:
    """Let the debounce elapse and the forecast run."""
    await asyncio.sleep(SHOP_DEBOUNCE * 2)
    if pipe._shop_task is not None:
        await asyncio.gather(pipe._shop_task, return_exceptions=True)


def test_a_burst_of_board_changes_runs_one_simulation():
    """Selling and re-buying fires a flurry of tag changes. Simulating each one
    would queue work the player has already moved past."""
    async def run():
        pipe, sim, seen = _pipeline()
        await pipe.handle([ev.TurnChange(turn=8), ev.NextOpponent(player_id=4)])
        for n in (1, 2, 3, 4):
            await pipe.handle([ev.ShopBoard(board=_board(1, minions=n), turn=8)])
        await _drain(pipe)
        return sim.calls, seen

    calls, seen = asyncio.run(run())
    assert len(calls) == 1, f"expected one coalesced simulation, got {len(calls)}"
    assert len(seen) == 1


def test_the_shop_forecast_is_cheaper_than_a_combat_forecast():
    """It re-runs constantly and is a guide, not the number of record."""
    async def run():
        pipe, sim, _ = _pipeline()
        await pipe.handle([ev.TurnChange(turn=8), ev.NextOpponent(player_id=4)])
        await pipe.handle([ev.ShopBoard(board=_board(1), turn=8)])
        await _drain(pipe)
        return sim.calls

    [sims] = asyncio.run(run())
    assert sims is not None and sims < 8000


def test_combat_cancels_a_pending_shop_forecast():
    """The real fight supersedes any guess about it and must not wait behind
    one holding the simulator's lock."""
    async def run():
        pipe, sim, seen = _pipeline()
        await pipe.handle([ev.TurnChange(turn=8), ev.NextOpponent(player_id=4)])
        await pipe.handle([ev.ShopBoard(board=_board(1), turn=8)])
        # Combat lands inside the debounce window, before the guess ever runs.
        snapshot = BoardSnapshot(turn=8, friendly=_board(1), opponent=_board(4))
        await pipe.handle([ev.CombatStart(snapshot=snapshot)])
        await _drain(pipe)
        return seen

    assert asyncio.run(run()) == []


def test_an_unscouted_opponent_gets_no_forecast():
    """Inventing a board would be worse than showing nothing."""
    async def run():
        pipe, sim, seen = _pipeline()
        await pipe.handle([ev.TurnChange(turn=8), ev.NextOpponent(player_id=99)])
        await pipe.handle([ev.ShopBoard(board=_board(1), turn=8)])
        await _drain(pipe)
        return sim.calls, seen

    calls, seen = asyncio.run(run())
    assert calls == [] and seen == []


def test_the_forecast_reports_how_old_the_scouted_board_is():
    async def run():
        pipe, sim, seen = _pipeline()
        await pipe.handle([ev.TurnChange(turn=9), ev.NextOpponent(player_id=4)])
        await pipe.handle([ev.ShopBoard(board=_board(1), turn=9)])
        await _drain(pipe)
        return seen

    [(event, prediction)] = asyncio.run(run())
    assert (event.turn, event.seen_turn) == (9, 6)
    assert prediction.won_percent == 55


def test_catch_up_does_not_simulate_combats_that_already_happened():
    """The tailer reads each session log from the top, so a restart mid-session
    replays every combat in it. Each one used to cost a full 8000-trial run,
    serialized, while the player is in a game."""
    async def run():
        pipe, sim, _ = _pipeline()
        snap = BoardSnapshot(turn=8, friendly=_board(1, minions=3), opponent=_board(4, minions=2))
        await pipe.handle([ev.CombatStart(snapshot=snap)], historical=True)
        return sim.calls

    assert asyncio.run(run()) == []


def test_a_live_combat_still_gets_odds():
    async def run():
        pipe, sim, _ = _pipeline()
        snap = BoardSnapshot(turn=8, friendly=_board(1, minions=3), opponent=_board(4, minions=2))
        await pipe.handle([ev.CombatStart(snapshot=snap)])
        return sim.calls

    assert asyncio.run(run()) == [None], "one live simulation, at the default trial count"


def test_catch_up_still_remembers_the_boards_it_saw():
    """Skipping the simulation must not skip opponent memory — the scout popout
    and the shop forecast are both built from boards seen in past combats."""
    async def run():
        pipe, sim, _ = _pipeline()
        snap = BoardSnapshot(turn=8, friendly=_board(1, minions=3), opponent=_board(7, minions=2))
        await pipe.handle([ev.CombatStart(snapshot=snap)], historical=True)
        return pipe.memory.last_seen(7)

    assert asyncio.run(run()) is not None


def test_the_shop_forecast_runs_in_the_background_lane():
    """A guide the player is shuffling minions against has no business taking
    the whole worker pool the real fight needs."""
    async def run():
        pipe, sim, _ = _pipeline()
        await pipe.handle([ev.TurnChange(turn=8), ev.NextOpponent(player_id=4)])
        await pipe.handle([ev.ShopBoard(board=_board(1, minions=2), turn=8)])
        await _drain(pipe)
        return sim.lanes

    assert asyncio.run(run()) == [True]


def test_the_combat_forecast_runs_in_the_foreground_lane():
    async def run():
        pipe, sim, _ = _pipeline()
        snap = BoardSnapshot(turn=8, friendly=_board(1, minions=3), opponent=_board(4, minions=2))
        await pipe.handle([ev.CombatStart(snapshot=snap)])
        return sim.lanes

    assert asyncio.run(run()) == [False]
