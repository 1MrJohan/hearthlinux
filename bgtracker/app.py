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
from bgtracker.diagnostics import status
from bgtracker.headless import print_event, render_board_line
from bgtracker.history.db import HistoryDB
from bgtracker.parse import events as ev
from bgtracker.screen.rating import RatingReader
from bgtracker.sim.client import (
    SimClient,
    SimResult,
    SimulatorUnavailable,
    UnsupportedCombatCardsError,
)
from bgtracker.sim.mapper import simulation_blocker, to_battle_info
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
    def __init__(
        self,
        sim: SimClient | None,
        db: HistoryDB | None,
        rating_reader: RatingReader | None = None,
        cfg=None,
    ):
        self.sim = sim
        self.db = db
        # None wherever the screen cannot belong to the game being handled — a
        # --replay, a test — or tesseract is missing. `cfg.mmr_screen_read` is
        # read fresh at each GameEnd, so the setting applies live.
        self.rating_reader = rating_reader
        self.cfg = cfg
        self.memory = OpponentMemory()
        self.listeners: list[Callable[[ev.Event, SimResult | None], None]] = []
        self._game_id: int | None = None
        self._turn = 0
        self._pending: tuple[BoardSnapshot, SimResult | None] | None = None
        self._next_opponent: int | None = None
        self._shop_board: PlayerBoard | None = None
        self._shop_task: asyncio.Task | None = None
        self._rating_task: asyncio.Task | None = None
        # The notice currently raised per kind, so each is fanned out once.
        self._notices: dict[str, str | None] = {}

    SIM_DOWN = "Combat simulator not running — odds are off"

    def notice(self, kind: str, text: str | None) -> None:
        """Raise (or, with None, clear) a tracker notice for the overlay.

        Deduplicated per kind, so a retry loop that keeps failing does not
        re-render the same line every half minute.
        """
        if self._notices.get(kind) == text:
            return
        self._notices[kind] = text
        for listener in self.listeners:
            listener(ev.TrackerNotice(kind=kind, text=text), None)

    async def handle(self, events: list[ev.Event], historical: bool = False) -> None:
        """Fan events out. `historical` marks a batch the tracker did not watch
        happen — the drain of a session log that already existed when it
        started — so it rebuilds state and memory without paying for odds
        nobody is waiting on.
        """
        for event in events:
            print_event(event)
            status.note_event(type(event).__name__)
            prediction = None
            # Events the pipeline derives rather than parses, fanned out after
            # the one that produced them.
            derived: ev.Event | None = None
            match event:
                case ev.GameStart(log_id=log_id):
                    # A game in the log proves logging works.
                    self.notice("log", None)
                    self.memory.reset()
                    self._pending = None
                    self._cancel_shop_forecast()
                    # Whatever is on screen now belongs to the new game.
                    self._cancel_rating_read()
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
                    self._shop_board = None
                    self.memory.record(snap.turn, snap.opponent)
                    prediction, reason = await self._simulate(snap, historical)
                    self._pending = (snap, prediction)
                    if prediction is None:
                        derived = ev.OddsUnavailable(reason=reason)
                case ev.CombatEnd(snapshot=end_snap, eliminated=eliminated):
                    derived = self._finish_combat(end_snap, eliminated)
                case ev.NextOpponent(player_id=pid):
                    self._next_opponent = pid
                    seen = self.memory.last_seen(pid)
                    if seen:
                        print(f"  last seen turn {seen.turn}: {render_board_line(seen.board)}")
                    if not historical:
                        self._schedule_shop_forecast()
                case ev.ShopBoard(board=board):
                    self._shop_board = board
                    if not historical:
                        self._schedule_shop_forecast()
                case ev.GameEnd(placement=place):
                    self._cancel_shop_forecast()
                    self._next_opponent = None
                    self._shop_board = None
                    game_id = self._game_id
                    if self.db and self._game_id:
                        self.db.end_game(self._game_id, place, final_turn=self._turn)
                        self._game_id = None
                    missed = self._start_rating_read(game_id, historical)
                    if missed is not None:
                        derived = ev.RatingMissed(reason=missed)
            for listener in self.listeners:
                listener(event, prediction)
            if derived is not None:
                print_event(derived)
                for listener in self.listeners:
                    listener(derived, None)

    def finish_catchup(self) -> None:
        """Forecast the one final shop state reconstructed from history.

        A bounded catch-up yields between slices, so its normal 300ms debounce
        could otherwise simulate obsolete intermediate boards.  Historical
        event handling only retains state; reaching the tailer's high-water
        mark calls this once to resume live background work.
        """
        self._schedule_shop_forecast()

    def reset_history(self, backup: bool = True):
        """Empty the match history without invalidating the live connection."""
        if self.db is None:
            return None
        saved = self.db.reset(backup)
        # The game currently being recorded no longer has a row to attach to.
        self._game_id = None
        return saved

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
        event: ev.ShopForecast | None = None
        result: SimResult | None = None
        try:
            await asyncio.sleep(SHOP_DEBOUNCE)
            seen = self.memory.last_seen(self._next_opponent)
            if seen is None or self._shop_board is None:
                return   # never scouted them: nothing honest to forecast against
            snapshot = BoardSnapshot(turn=self._turn, friendly=self._shop_board,
                                     opponent=seen.board)
            event = ev.ShopForecast(
                opponent_id=self._next_opponent, seen_turn=seen.turn, turn=self._turn
            )
            info = to_battle_info(snapshot)
            if info is not None:
                result = await self.sim.simulate(
                    info, sims=SHOP_SIM_COUNT, background=True
                )
                _apply_damage_cap(result, snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A shop forecast is a convenience; failing one must never disturb
            # the tailer or the combat path. Still publish the empty result:
            # the board may have changed from supported to unsupported, and
            # leaving the previous number visible would attach stale odds to it.
            log.debug("shop forecast failed: %r", exc)
        if event is not None:
            for listener in self.listeners:
                listener(event, result)

    def _start_rating_read(self, game_id: int | None, historical: bool) -> str | None:
        """Start reading the post-game screen; the reason it can't, otherwise."""
        # A game that ended while the tracker was not watching: whatever is on
        # screen now belongs to something else.
        if historical:
            return "caught up from the log"
        if self.rating_reader is None:
            return "screen reader unavailable"
        if self.cfg is not None and not self.cfg.mmr_screen_read:
            return "screen reading is off"
        if self.db is None or game_id is None:
            return "game not recorded"
        self._cancel_rating_read()
        try:
            self._rating_task = asyncio.get_running_loop().create_task(
                self._read_rating(game_id)
            )
        except RuntimeError:
            self._rating_task = None
            return "no event loop"
        return None

    def _cancel_rating_read(self) -> None:
        if self._rating_task is not None and not self._rating_task.done():
            self._rating_task.cancel()
        self._rating_task = None

    async def _read_rating(self, game_id: int) -> None:
        event: ev.Event = ev.RatingMissed(reason="not seen on screen")
        try:
            reading = await self.rating_reader.watch(
                previous_rating=self.db.last_rating() if self.db else None
            )
            if reading is not None and self.db is not None:
                # A lobby snapshot has no delta; history gives the game one
                # only when no other game sits between it and the last reading.
                self.db.record_rating(
                    reading.rating, game_id=game_id, delta=reading.delta,
                    source="screen" if reading.delta is not None else "lobby",
                )
                event = ev.RatingRead(rating=reading.rating, delta=reading.delta)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Like the shop forecast: a convenience that must never disturb the
            # tailer. The miss still goes out, so the manual nudge takes over.
            log.warning("reading the rating off the screen failed: %r", exc)
        print_event(event)
        for listener in self.listeners:
            listener(event, None)

    async def _simulate(
        self, snapshot: BoardSnapshot, historical: bool = False
    ) -> tuple[SimResult | None, str]:
        """The forecast, or None and a short reason the HUD can show."""
        # A combat that already happened cannot be forecast, and the player is
        # not waiting on it. Ahead of the board check so a catch-up prints
        # nothing at all.
        if historical:
            return None, "tracker started mid-fight"
        if self.sim is None:
            return None, "simulator not running"
        info = to_battle_info(snapshot)
        if info is None:
            blocker = simulation_blocker(snapshot) or "unsupported combat state"
            print(f"  odds: n/a ({blocker})")
            return None, describe_blocker(blocker)
        # Before the await: the run can take seconds, and until something says
        # combat began the HUD would still show the recruit phase.
        for listener in self.listeners:
            listener(ev.CombatSimulating(snapshot=snapshot), None)
        def show_partial(provisional: SimResult) -> None:
            # A heavy 7v7 board takes seconds; this puts a usable number on
            # screen in a fraction of that and tightens it in place.
            _apply_damage_cap(provisional, snapshot)
            for listener in self.listeners:
                listener(ev.CombatForecast(snapshot=snapshot), provisional)

        try:
            result = await self.sim.simulate(info, on_partial=show_partial)
            status.sidecar_up = True
            self.notice("sim", None)
            _apply_damage_cap(result, snapshot)
            status.note_sim(result)
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
            return result, ""
        except Exception as exc:
            log.warning("simulation failed: %r", exc)
            print("  odds: unavailable")
            if isinstance(exc, SimulatorUnavailable):
                status.sidecar_up = False
                self.notice("sim", self.SIM_DOWN)
            return None, describe_failure(exc)

    def _finish_combat(
        self, end_snap: BoardSnapshot, eliminated: frozenset[int] = frozenset()
    ) -> ev.CombatResult | None:
        if self._pending is None:
            return None
        start_snap, prediction = self._pending
        self._pending = None
        outcome = _classify_outcome(start_snap, end_snap, eliminated)
        if self.db and self._game_id:
            self.db.record_combat(self._game_id, start_snap, prediction, outcome)
        return ev.CombatResult(
            turn=start_snap.turn,
            outcome=outcome,
            damage=_combat_damage(start_snap, end_snap),
        )


def describe_blocker(blocker: str) -> str:
    """`simulation_blocker`'s wording, for a player rather than a log."""
    def powers(text: str) -> str:
        return ", ".join(cards.name(p.strip()) for p in text.split(","))

    for prefix, render in (
        ("combat hero power not modeled: ", lambda rest: f"{powers(rest)} isn't simulated yet"),
        ("hero power state not recorded: ", lambda rest: f"{powers(rest)} state not recorded"),
    ):
        if blocker.startswith(prefix):
            return render(blocker[len(prefix):])
    return {
        "combat secret identity hidden": "a secret is hidden",
        "deity secret incomplete": "Old God secret incomplete",
        "board incomplete": "a board isn't fully visible",
    }.get(blocker, blocker)


def describe_failure(exc: Exception) -> str:
    """Why a run that did start produced nothing, in a few words."""
    if isinstance(exc, SimulatorUnavailable):
        return "simulator not running"
    if isinstance(exc, UnsupportedCombatCardsError):
        names = [cards.name(c) for c in exc.card_ids]
        shown = ", ".join(names[:2]) + ("…" if len(names) > 2 else "")
        return f"simulator can't model {shown}" if names else "simulator can't model this board"
    if isinstance(exc, TimeoutError):
        return "simulator timed out"
    if "died" in str(exc):
        return "simulator crashed"
    return "simulator error"


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


def _classify_outcome(
    start: BoardSnapshot, end: BoardSnapshot, eliminated: frozenset[int] = frozenset()
) -> str | None:
    """Win/tie/loss from hero HP deltas across the combat.

    `eliminated` is the PLAYER_IDs dead by CombatEnd. A lethal takes the
    opponent's hero out of PLAY before then, so `end.opponent` is None exactly
    when the fight was won outright — the one case HP deltas cannot see.
    """
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
    if start.opponent and start.opponent.bg_player_id in eliminated:
        # After the ghost check on purpose: a ghost is a dead player's entity,
        # so its id is always in the set without this fight having killed it.
        return "win"
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
