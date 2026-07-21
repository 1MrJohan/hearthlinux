"""Event pipeline shared by live and replay modes.

Consumes parser events and fans them out to: console output, the combat
simulator, opponent-board memory, and (optionally) the match-history DB.
The overlay subscribes to the same pipeline via `listeners`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from bgtracker.data import cards
from bgtracker.headless import print_event, render_board_line
from bgtracker.history.db import HistoryDB
from bgtracker.parse import events as ev
from bgtracker.sim.client import SimClient, SimResult
from bgtracker.sim.mapper import to_battle_info
from bgtracker.state.game import BoardSnapshot
from bgtracker.state.opponents import OpponentMemory

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, sim: SimClient | None, db: HistoryDB | None):
        self.sim = sim
        self.db = db
        self.memory = OpponentMemory()
        self.listeners: list[Callable[[ev.Event, SimResult | None], None]] = []
        self._game_id: int | None = None
        self._turn = 0
        self._pending: tuple[BoardSnapshot, SimResult | None] | None = None

    async def handle(self, events: list[ev.Event]) -> None:
        for event in events:
            print_event(event)
            prediction = None
            match event:
                case ev.GameStart():
                    self.memory.reset()
                    self._pending = None
                    if self.db:
                        self._game_id = self.db.start_game()
                case ev.TurnChange(turn=t):
                    self._turn = t
                case ev.HeroPicked(card_id=cid):
                    if self.db and self._game_id:
                        self.db.set_hero(self._game_id, cid)
                case ev.CombatStart(snapshot=snap):
                    self.memory.record(snap.turn, snap.opponent)
                    prediction = await self._simulate(snap)
                    self._pending = (snap, prediction)
                case ev.CombatEnd(snapshot=end_snap):
                    self._finish_combat(end_snap)
                case ev.NextOpponent(player_id=pid):
                    seen = self.memory.last_seen(pid)
                    if seen:
                        print(f"  last seen turn {seen.turn}: {render_board_line(seen.board)}")
                case ev.GameEnd(placement=place):
                    if self.db and self._game_id:
                        self.db.end_game(self._game_id, place, final_turn=self._turn)
                        self._game_id = None
            for listener in self.listeners:
                listener(event, prediction)

    async def _simulate(self, snapshot: BoardSnapshot) -> SimResult | None:
        if self.sim is None:
            return None
        info = to_battle_info(snapshot)
        if info is None:
            print("  odds: n/a (board incomplete)")
            return None
        try:
            result = await self.sim.simulate(info)
            print(
                f"  odds: {result}  "
                f"(dmg dealt ~{result.avg_damage_won:.0f} / taken ~{result.avg_damage_lost:.0f})"
            )
            return result
        except Exception as exc:
            log.warning("simulation failed: %r", exc)
            print("  odds: unavailable")
            return None

    def _finish_combat(self, end_snap: BoardSnapshot) -> None:
        if self._pending is None:
            return
        start_snap, prediction = self._pending
        self._pending = None
        outcome = _classify_outcome(start_snap, end_snap)
        if self.db and self._game_id:
            self.db.record_combat(self._game_id, start_snap, prediction, outcome)


def _classify_outcome(start: BoardSnapshot, end: BoardSnapshot) -> str | None:
    """Win/tie/loss from hero HP deltas across the combat."""
    if start.friendly and end.friendly:
        my_delta = (end.friendly.health + end.friendly.armor) - (
            start.friendly.health + start.friendly.armor
        )
        if my_delta < 0:
            return "loss"
        if (
            start.opponent
            and end.opponent
            and end.opponent.player_id == start.opponent.player_id
        ):
            opp_delta = (end.opponent.health + end.opponent.armor) - (
                start.opponent.health + start.opponent.armor
            )
            if opp_delta < 0:
                return "win"
            return "tie"
        # our HP unchanged but opponent unknown post-combat: win or tie
        return None
    return None


def hero_name(card_id: str | None) -> str:
    return cards.name(card_id)
