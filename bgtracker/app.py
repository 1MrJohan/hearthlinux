"""Event pipeline shared by live and replay modes.

Consumes parser events and fans them out to: console output, the combat
simulator, opponent-board memory, and (optionally) the match-history DB.
The overlay subscribes to the same pipeline via `listeners`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bgtracker.data import cards
from bgtracker.headless import print_event, render_board_line
from bgtracker.history.db import HistoryDB
from bgtracker.parse import events as ev
from bgtracker.sim.client import SimClient, SimResult
from bgtracker.sim.mapper import to_battle_info
from bgtracker.state.game import BoardSnapshot, PlayerBoard, is_ghost
from bgtracker.state.opponents import OpponentMemory

log = logging.getLogger(__name__)


# Buying, selling and repositioning arrive as bursts of tag changes, and each
# one would otherwise start a simulation. Long enough to coalesce a burst, short
# enough that the number feels like it belongs to the board in front of you.
SHOP_DEBOUNCE = 0.30

# Shop odds are a guide while you shuffle minions around, not the number of
# record, and they are re-run constantly. Precision matters far less here than
# staying out of the way of the combat forecast.
SHOP_SIM_COUNT = 2000


class Pipeline:
    def __init__(self, sim: SimClient | None, db: HistoryDB | None):
        self.sim = sim
        self.db = db
        self.memory = OpponentMemory()
        self.listeners: list[Callable[[ev.Event, SimResult | None], None]] = []
        self._game_id: int | None = None
        self._turn = 0
        self._pending: tuple[BoardSnapshot, SimResult | None] | None = None
        self._next_opponent: int | None = None
        self._shop_board: PlayerBoard | None = None
        self._shop_task: asyncio.Task | None = None

    async def handle(self, events: list[ev.Event]) -> None:
        for event in events:
            print_event(event)
            prediction = None
            # Events the pipeline derives rather than parses, fanned out after
            # the one that produced them.
            derived: ev.Event | None = None
            match event:
                case ev.GameStart(log_id=log_id):
                    self.memory.reset()
                    self._pending = None
                    self._cancel_shop_forecast()
                    self._next_opponent = None
                    self._shop_board = None
                    if self.db:
                        self._game_id = self.db.start_game(log_id)
                case ev.TurnChange(turn=t):
                    self._turn = t
                case ev.HeroPicked(card_id=cid):
                    if self.db and self._game_id:
                        self.db.set_hero(self._game_id, cid)
                case ev.CombatStart(snapshot=snap):
                    # The real fight supersedes any guess about it, and must
                    # never queue behind one.
                    self._cancel_shop_forecast()
                    self.memory.record(snap.turn, snap.opponent)
                    prediction = await self._simulate(snap)
                    self._pending = (snap, prediction)
                case ev.CombatEnd(snapshot=end_snap):
                    derived = self._finish_combat(end_snap)
                case ev.NextOpponent(player_id=pid):
                    self._next_opponent = pid
                    seen = self.memory.last_seen(pid)
                    if seen:
                        print(f"  last seen turn {seen.turn}: {render_board_line(seen.board)}")
                    self._schedule_shop_forecast()
                case ev.ShopBoard(board=board):
                    self._shop_board = board
                    self._schedule_shop_forecast()
                case ev.GameEnd(placement=place):
                    self._cancel_shop_forecast()
                    if self.db and self._game_id:
                        self.db.end_game(self._game_id, place, final_turn=self._turn)
                        self._game_id = None
            for listener in self.listeners:
                listener(event, prediction)
            if derived is not None:
                print_event(derived)
                for listener in self.listeners:
                    listener(derived, None)

    # -- shop-phase forecast -------------------------------------------
    def _cancel_shop_forecast(self) -> None:
        if self._shop_task is not None:
            self._shop_task.cancel()
            self._shop_task = None

    def _schedule_shop_forecast(self) -> None:
        """(Re)start the debounce. The newest board always wins."""
        if self.sim is None or self._shop_board is None or self._next_opponent is None:
            return
        self._cancel_shop_forecast()
        try:
            self._shop_task = asyncio.get_running_loop().create_task(self._shop_forecast())
        except RuntimeError:
            self._shop_task = None   # no loop (sync replay): shop odds are moot

    async def _shop_forecast(self) -> None:
        try:
            await asyncio.sleep(SHOP_DEBOUNCE)
            seen = self.memory.last_seen(self._next_opponent)
            if seen is None or self._shop_board is None:
                return   # never scouted them: nothing honest to forecast against
            snapshot = BoardSnapshot(turn=self._turn, friendly=self._shop_board,
                                     opponent=seen.board)
            info = to_battle_info(snapshot)
            if info is None:
                return
            result = await self.sim.simulate(info, sims=SHOP_SIM_COUNT)
            _apply_damage_cap(result, snapshot)
            event = ev.ShopForecast(
                opponent_id=self._next_opponent, seen_turn=seen.turn, turn=self._turn
            )
            for listener in self.listeners:
                listener(event, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A shop forecast is a convenience; failing one must never disturb
            # the tailer or the combat path.
            log.debug("shop forecast failed: %r", exc)

    async def _simulate(self, snapshot: BoardSnapshot) -> SimResult | None:
        if self.sim is None:
            return None
        info = to_battle_info(snapshot)
        if info is None:
            print("  odds: n/a (board incomplete)")
            return None
        def show_partial(provisional: SimResult) -> None:
            # A heavy 7v7 board takes seconds; this puts a usable number on
            # screen in a fraction of that and tightens it in place.
            _apply_damage_cap(provisional, snapshot)
            for listener in self.listeners:
                listener(ev.CombatForecast(snapshot=snapshot), provisional)

        try:
            result = await self.sim.simulate(info, on_partial=show_partial)
            _apply_damage_cap(result, snapshot)
            cap_note = f", cap {snapshot.damage_cap}" if snapshot.damage_cap else ""
            margin = result.margin
            sample = f"{result.sims_run} sims in {result.sim_ms:.0f}ms"
            if margin is not None:
                sample = f"±{margin:.1f}%, {sample}"
            lethal = (
                f"  ☠ lethal {result.lost_lethal_percent:.0f}%"
                if result.lost_lethal_percent >= 1.0 else ""
            )
            print(
                f"  odds: {result}  "
                f"(dmg dealt {result.damage_dealt_text} / taken {result.damage_taken_text}{cap_note})"
                f"  [{sample}]{lethal}"
            )
            return result
        except Exception as exc:
            log.warning("simulation failed: %r", exc)
            print("  odds: unavailable")
            return None

    def _finish_combat(self, end_snap: BoardSnapshot) -> ev.CombatResult | None:
        if self._pending is None:
            return None
        start_snap, prediction = self._pending
        self._pending = None
        outcome = _classify_outcome(start_snap, end_snap)
        if self.db and self._game_id:
            self.db.record_combat(self._game_id, start_snap, prediction, outcome)
        return ev.CombatResult(
            turn=start_snap.turn,
            outcome=outcome,
            damage=_combat_damage(start_snap, end_snap),
        )


def _combat_damage(start: BoardSnapshot, end: BoardSnapshot) -> int:
    """HP that actually changed hands, whichever side took it.

    Only one player takes damage in a combat, so the first side found to have
    lost health is the answer — already capped, since this reads the HP the
    game itself applied rather than anything the simulator projected.
    """
    for before, after in ((start.friendly, end.friendly), (start.opponent, end.opponent)):
        if before and after:
            delta = (after.health + after.armor) - (before.health + before.armor)
            if delta < 0:
                return -delta
    return 0


def _clamp_range(spread, cap: int):
    return None if spread is None else (min(spread[0], cap), min(spread[1], cap))


def _apply_damage_cap(result: SimResult, snapshot: BoardSnapshot) -> None:
    """Fold BACON_COMBAT_DAMAGE_CAP into an uncapped simulator result, in place.

    The simulator does not model the early-game damage cap, so every damage
    figure it reports has to be clipped before it is shown or recorded.
    """
    cap = snapshot.damage_cap
    if not cap:
        return
    result.avg_damage_won = min(result.avg_damage_won, cap)
    result.avg_damage_lost = min(result.avg_damage_lost, cap)
    result.damage_won_range = _clamp_range(result.damage_won_range, cap)
    result.damage_lost_range = _clamp_range(result.damage_lost_range, cap)
    # A capped hit that cannot reach your total health cannot kill you, no
    # matter what the uncapped run counted as lethal. Exact, not a heuristic.
    if snapshot.friendly and cap < snapshot.friendly.health + snapshot.friendly.armor:
        result.lost_lethal_percent = 0.0
    if snapshot.opponent and cap < snapshot.opponent.health + snapshot.opponent.armor:
        result.won_lethal_percent = 0.0


def _classify_outcome(start: BoardSnapshot, end: BoardSnapshot) -> str | None:
    """Win/tie/loss from hero HP deltas across the combat."""
    if start.friendly is None or end.friendly is None:
        return None
    my_delta = (end.friendly.health + end.friendly.armor) - (
        start.friendly.health + start.friendly.armor
    )
    if my_delta < 0:
        # Deliberately ahead of the ghost check: a ghost can cost you HP, and
        # HP that is really gone is a real loss.
        return "loss"
    if is_ghost(start.opponent):
        # A ghost's hero HP reads 0 or negative, so it cannot confirm a win —
        # this is "did not lose, cannot say more", not "nothing happened".
        return "ghost"
    if (
        start.opponent
        and end.opponent
        # bg_player_id is the hero's PLAYER_ID tag — the stable identity.
        # player_id is the log CONTROLLER, a slot every opponent shares, so
        # comparing it matches any opponent and reads a stranger's untouched
        # HP as a damage-free tie.
        and end.opponent.bg_player_id == start.opponent.bg_player_id
    ):
        opp_delta = (end.opponent.health + end.opponent.armor) - (
            start.opponent.health + start.opponent.armor
        )
        if opp_delta < 0:
            return "win"
        return "tie"
    # our HP unchanged but the opponent is unknown or somebody else: win or tie
    return None


def hero_name(card_id: str | None) -> str:
    return cards.name(card_id)
