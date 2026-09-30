"""GTK4 overlay application, sharing one event loop with asyncio.

PyGObject >= 3.50 lets GLib's main loop *be* the asyncio loop via
gi.events.GLibEventLoopPolicy — the tailer, sim client, and UI all run
single-threaded on it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine

import gi

gi.require_version("Gtk", "4.0")
from gi.events import GLibEventLoopPolicy  # noqa: E402
from gi.repository import Gtk  # noqa: E402

from bgtracker.data import cards  # noqa: E402
from bgtracker.parse import events as ev  # noqa: E402
from bgtracker.sim.client import SimResult  # noqa: E402

from .model import BoardView, OverlayState, render  # noqa: E402
from .window import OverlayWindow  # noqa: E402

log = logging.getLogger(__name__)

# How long the end-of-game MMR nudge stays up. It is a hole in the overlay's
# click-through guarantee for exactly as long as it is visible, so an ignored
# one must not sit over the game's menus for the rest of the session.
MMR_PROMPT_SECONDS = 120


def _hero_meta(board) -> str:
    """`18 HP · Tavern 3` — the dim line beside the phase title."""
    if board is None:
        return ""
    return f"{board.health + board.armor} HP · Tavern {board.tier}"


def _standing_meta(you) -> str:
    """The same line as `_hero_meta`, off your live leaderboard row."""
    tier = f" · Tavern {you.tier}" if you.tier else ""
    return f"{you.total_health} HP{tier}"


def _odds_line(prediction) -> str:
    """`Win 41 · Tie 12 · Loss 47 · ☠ 9%` — lethal only when there is any."""
    line = (
        f"Win {prediction.won_percent:.0f}  ·  Tie {prediction.tied_percent:.0f}"
        f"  ·  Loss {prediction.lost_percent:.0f}"
    )
    # Below 1% it would print as "☠ 0%" on every safe board.
    if prediction.lost_lethal_percent >= 1:
        line += f"  ·  ☠ {prediction.lost_lethal_percent:.0f}%"
    return line


def _combat_meta(snapshot) -> str:
    """`18 HP · vs Tickatus` — your HP and who you are up against."""
    you, opponent = snapshot.friendly, snapshot.opponent
    hp = f"{you.health + you.armor} HP" if you else ""
    versus = f"vs {cards.name(opponent.hero_card_id)}" if opponent else ""
    return " · ".join(part for part in (hp, versus) if part)


class OverlayApp:
    """Owns the Gtk.Application + window; listener plugs into the Pipeline."""

    # Class-level defaults so a bare __new__ (tests) still has them.
    _in_combat = False
    _shop_meta = ""
    # Your own leaderboard row. It is re-emitted whenever your hero or tier
    # changes, so it is what keeps the recruit meta line current after you
    # level mid-phase — the combat snapshot only fixes it at the last fight.
    _you: ev.Standing | None = None
    _shop_result: tuple[str | None, int] = (None, 0)
    _mmr_source: int | None = None
    # The odds half of the next-opponent line, kept apart from its age so a
    # recalculation can relabel it without re-parsing the text.
    _next_odds: str | None = None
    # Turn of the fight whose partial odds are on screen, so a run that then
    # fails can keep them as provisional instead of wiping real numbers.
    _partial_turn: int | None = None
    # Tracker notices by kind. Replaced, never mutated, so the class-level
    # default is safe to share across bare __new__ instances in tests.
    _notices: dict[str, str | None] = {}

    def __init__(self, settings=None):
        from gi.repository import Gio

        from bgtracker.config import load_config
        from bgtracker.settings import SettingsService

        # NON_UNIQUE: a stale instance must never make a new launch silently
        # defer to it and exit.
        self.app = Gtk.Application(
            application_id="dev.bgtracker.overlay",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        # The one Config for the process arrives with the service; --demo and
        # the tests construct an OverlayApp on their own, so fall back to a
        # private one rather than requiring a caller to build it.
        self.settings = settings or SettingsService(load_config())
        self.window: OverlayWindow | None = None
        self.hover = None
        self.pipeline = None  # set by the caller for opponent-memory lookups
        # Event bursts update retained state synchronously but need only one
        # GTK diff on the next idle turn.  Bare __new__ test instances omit
        # this flag and retain immediate rendering.
        self._defer_flush = True
        self.reset_state()
        self.app.connect("activate", self._on_activate)

    def reset_state(self) -> None:
        """Start with an empty display and nothing rendered yet.

        `_rendered` is what the live window was last told; keeping it beside
        `state` is what lets `render` emit only the difference.
        """
        self.state = OverlayState()
        self._rendered: OverlayState | None = OverlayState()
        self._flush_source: int | None = None
        # GameState advances TURN as soon as its sub-second combat resolution
        # finishes, long before PowerTaskList says the player has finished
        # watching that fight. Keep the real turn independently from the one
        # captioning the phase currently visible on screen.
        self._current_turn: int | None = None

    def _on_hover_slot(self, slot: int | None) -> None:
        st = self.state
        standings = st.standings
        # Slot i is leaderboard position i+1; match by place, not list index.
        entry = (
            next((e for e in standings if e.place == (slot + 1)), None)
            if slot is not None else None
        )
        st.hot_place = entry.place if entry else None
        if entry is None:
            if slot is not None:
                log.debug(
                    "hover-lookup: slot %s -> no standings entry (standings=%s)",
                    slot, standings,
                )
        self._refresh_hover_board()
        self._queue_flush()

    def _refresh_hover_board(self) -> None:
        """Rebuild the scout popout from retained hover and opponent state.

        Pointer callbacks only fire when the hovered slot changes. Events can
        change the player, remembered board, or forecast under a stationary
        pointer, so those event handlers call this helper too.
        """
        st = self.state
        entry = next((e for e in st.standings if e.place == st.hot_place), None)
        if entry is None:
            st.hover_board = None
            return
        seen = self.pipeline.memory.last_seen(entry.player_id) if self.pipeline else None
        # DEBUG, not INFO: this fires on every pointer move across the rail, and
        # log_to_file is on by default — at INFO it churns the rotating log for
        # nothing. `hover_debug` already advertises itself as what turns slot
        # logging on, and the log level is what does that.
        log.debug(
            "hover-lookup: place %s %s pid=%s seen=%s",
            entry.place, entry.hero_card_id, entry.player_id,
            f"turn {seen.turn}" if seen else None,
        )
        is_next = entry.player_id == st.next_opponent_id
        if entry.dead:
            detail = "eliminated"
        elif seen:
            detail = f"last seen · turn {seen.turn}"
        elif entry.you:
            detail = "this is you"
        else:
            detail = "not scouted yet"
        status = f"next opponent · {detail}" if is_next else detail
        st.hover_board = BoardView(
            f"#{entry.place} {cards.name(entry.hero_card_id)}",
            status,
            seen.board if seen else None,
            hero_card_id=entry.hero_card_id,
            dead=entry.dead,
            forecast=st.next_forecast if is_next else None,
        )

    def _on_activate(self, app):
        from . import theme

        # Must precede any widget construction: Pango caches the face it picks
        # for a description, so one lookup before registration would pin the
        # whole overlay to the fallback font for the process's lifetime.
        theme.register_fonts()
        self._build_window()
        self._build_hover()
        self._subscribe()

    # -- construction, done in a way that can be repeated ----------------
    def _build_window(self) -> None:
        """(Re)build the overlay window and repaint it from state."""
        from . import theme

        cfg = self.settings.cfg
        old = self.window
        self.window = OverlayWindow(
            application=self.app, cfg=cfg,
            on_settings=self.open_settings,
            on_edit=lambda value: self.settings.set("overlay_edit", value),
            on_mmr=self.open_history,
        )
        self.window.present()
        if old is not None:
            # The scale is baked into a display-wide CSS provider; leaving the
            # old one attached would let whichever loaded first keep winning.
            theme.uninstall(old.css_provider)
            old.destroy()
        # No `previous`: a fresh window shows nothing, so everything is emitted.
        self._rendered = None
        self._flush()

    def _build_hover(self) -> None:
        """(Re)build the leaderboard hover column, if it is enabled at all."""
        cfg = self.settings.cfg
        if self.hover is not None:
            # Before destroy(): the X11 pointer poll is a GLib source, which
            # outlives the window unless it is cancelled. This method runs on
            # every HOVER-channel change, so leaking one poll per call means a
            # handful of calibration nudges leaves several of them fighting
            # over the scout popout with stale geometry.
            self.hover.stop()
            self.hover.destroy()
            self.hover = None
        if not cfg.hover_strips:
            return
        from .hover import HoverStrips

        # Rebound to the *current* window every time. These are bound methods
        # on a specific OverlayWindow, so a rebuild that skipped this would
        # leave the hover column resolving slots against destroyed geometry.
        self.hover = HoverStrips(
            application=self.app, cfg=cfg, on_slot=self._on_hover_slot,
            rail_rect=self.window.rail_rect,
            hud_rect=self.window.hud_rect,
            on_hud=self._on_hud_hover,
        )

    def _subscribe(self) -> None:
        from bgtracker import settings as sett

        self.settings.subscribe(sett.OVERLAY_REBUILD, self._apply_rebuild)
        self.settings.subscribe(sett.OVERLAY, self._apply_overlay)
        self.settings.subscribe(sett.HOVER, self._apply_hover)

    def _apply_rebuild(self, _keys) -> None:
        self._build_window()
        self._build_hover()

    def _apply_overlay(self, keys) -> None:
        # Turning the nudge off has to take a visible one down with it, or this
        # would be the one setting in the window that waits for the next game.
        if "mmr_prompt" in keys and not self.settings.cfg.mmr_prompt:
            self._hide_mmr_prompt()
        if "overlay_edit" in keys and self.window is not None:
            self.window.set_edit(self.settings.cfg.overlay_edit)
            # Layout mode changes the hover column from click-through boxes to
            # draggable ones, which it decides once at construction.
            self._build_hover()

    def _apply_hover(self, _keys) -> None:
        self._build_hover()

    def _on_hud_hover(self, hovered: bool) -> None:
        # Only the HUD. This used to also present the hover window, which was
        # never about hovering: that call was the last line of the HoverStrips
        # construction block until this method was inserted directly above it
        # and swallowed it. Construction was then left without a present, and an
        # unrealized window is what made rebuilding the hover column segfault.
        # Presentation now belongs to HoverStrips itself.
        if self.window is not None:
            self.window.set_hud_hovered(hovered)

    def open_settings(self) -> None:
        from .settings_window import SettingsWindow

        # Resolved at click time: the pipeline is attached after construction,
        # and deleting the history has to go through the one holding the open
        # database connection.
        reset_history = self.pipeline.reset_history if self.pipeline else None
        SettingsWindow.open(self.app, self.settings, on_reset_history=reset_history)

    def open_history(self) -> None:
        """The nudge's destination: match history, rating box focused."""
        from .history_window import HistoryWindow

        # Down before the window is up: the nudge has done its job, and leaving
        # it behind would keep a rect of the overlay swallowing clicks.
        self._hide_mmr_prompt()
        HistoryWindow.open(self.app, focus_rating=True)

    # -- the end-of-game MMR nudge ---------------------------------------
    def _show_mmr_prompt(self) -> None:
        if not self.settings.cfg.mmr_prompt:
            return
        self.state.mmr_prompt = True
        self._arm_mmr_timeout()

    def _hide_mmr_prompt(self) -> None:
        self._cancel_mmr_timeout()
        if self.state.mmr_prompt:
            self.state.mmr_prompt = False
            self._queue_flush()

    def _arm_mmr_timeout(self) -> None:
        self._cancel_mmr_timeout()
        try:
            from gi.repository import GLib
        except ImportError:      # tests without a GTK stack
            return
        self._mmr_source = GLib.timeout_add_seconds(
            MMR_PROMPT_SECONDS, self._mmr_timed_out
        )

    def _cancel_mmr_timeout(self) -> None:
        if self._mmr_source is not None:
            from gi.repository import GLib

            GLib.source_remove(self._mmr_source)
            self._mmr_source = None

    def _mmr_timed_out(self) -> bool:
        self._mmr_source = None
        self.state.mmr_prompt = False
        self._queue_flush()
        return False

    def _notice_text(self) -> str | None:
        # A missing log outranks a missing simulator: nothing works without it.
        for kind in ("log", "sim"):
            if self._notices.get(kind):
                return self._notices[kind]
        return None

    def _recruit_meta(self) -> str:
        # The combat snapshot is the fallback only: it is frozen at the last
        # fight, so it would still say Tavern 3 after a mid-phase level up.
        return _standing_meta(self._you) if self._you else self._shop_meta

    # Pipeline listener -------------------------------------------------
    def on_event(self, event: ev.Event, prediction: SimResult | None) -> None:
        # State updates must happen even before the window exists — events
        # streamed during startup replay would otherwise be lost.
        st = self.state
        match event:
            case ev.GameStart():
                self._next_odds = None
                self._hide_mmr_prompt()
                self._in_combat = False
                self._current_turn = None
                self._shop_result = (None, 0)
                self._shop_meta = ""
                self._you = None
                self._clear_to_idle(st)
                st.phase = ("Hero Select", "")
                st.status = "Waiting — choose your hero"
            case ev.HeroPicked(card_id=cid):
                st.status = f"Playing {cards.name(cid)}"
            case ev.TurnChange(turn=t):
                self._current_turn = t
                if not self._in_combat:
                    st.turn = t
                    # The first recruit phase has no fight before it, so no
                    # ShopReady ends hero select; the first turn does. Not a
                    # ShopBoard: that already fires while heroes are offered.
                    if st.phase[0] == "Hero Select":
                        st.phase = ("Recruit Phase", self._recruit_meta())
                        st.status = ""
            case ev.CombatSimulating(snapshot=s):
                # Combat began and a run is under way. Enter the combat view
                # now: until the first partial, the HUD would otherwise keep
                # the recruit title and the previous fight's result.
                self._in_combat = True
                self._current_turn = s.turn
                self._partial_turn = None
                self._enter_combat(st, s)
                self._clear_forecast(st)
                st.status = "Simulating…"
                self._refresh_hover_board()
            case ev.CombatForecast(snapshot=s) if prediction is not None:
                # A run still tightening. Same widgets, same treatment — the
                # numbers simply firm up in place instead of appearing late.
                self._in_combat = True
                self._current_turn = s.turn
                self._partial_turn = s.turn
                self._enter_combat(st, s)
                self._set_forecast(st, prediction)
                self._refresh_hover_board()
            case ev.CombatStart(snapshot=s):
                self._in_combat = True
                self._current_turn = s.turn
                self._enter_combat(st, s)
                if prediction is not None:
                    self._set_forecast(st, prediction)
                elif self._partial_turn == s.turn:
                    # The run failed after partials: they are real numbers
                    # from this fight, so keep them and say what they are.
                    st.status = "Provisional"
                else:
                    self._clear_forecast(st)
                    st.status = "No odds"
                self._partial_turn = None
                self._refresh_hover_board()
            case ev.TrackerNotice(kind=kind, text=text):
                self._notices = {**self._notices, kind: text}
                # Idle only: in a game the per-fight reasons already say what
                # is wrong, and a notice must not overwrite the phase status.
                if st.phase[0] == "":
                    st.status = self._notice_text() or "waiting for game…"
            case ev.OddsUnavailable(reason=reason) if self._in_combat:
                # Follows the CombatStart above; names the cause the log has.
                prefix = st.status or "No odds"
                st.status = f"{prefix} · {reason}" if reason else prefix
            case ev.Buffs(entries=e, shop=shop, gold_next_turn=g, free_rerolls=fr):
                st.buffs = (e, shop, g, fr)
            case ev.Standings(places=places):
                st.standings = places
                self._you = next((p for p in places if p.you), self._you)
                # Only outside combat: mid-fight, your row takes the damage
                # while the battle is still animating, and would spoil it.
                if not self._in_combat and st.phase[0] == "Recruit Phase":
                    st.phase = ("Recruit Phase", self._recruit_meta())
                self._refresh_hover_board()
            case ev.CombatEnd(snapshot=s):
                # The engine has resolved the fight, but the client is still
                # animating it for another 20-45s. Bank the post-combat state
                # and leave the display alone until ShopReady.
                self._shop_meta = _hero_meta(s.friendly)
            case ev.CombatResult(outcome=outcome, damage=damage):
                # Banked, never shown here: this lands with CombatEnd, while
                # the player is still watching the battle play out. Revealing
                # it now tells them who won before they have seen it.
                self._shop_result = (outcome, damage)
            case ev.ShopReady() if self._in_combat:
                self._in_combat = False
                st.turn = self._current_turn
                st.phase = ("Recruit Phase", self._recruit_meta())
                st.status = ""
                st.combat = False
                st.forecast_live = False
                # The forecast stays open through the recruit phase now, with
                # what actually happened underneath it — a fight only becomes
                # checkable once it is over.
                st.result = self._shop_result
                self._shop_result = (None, 0)
                # Defensive: _enter_combat no longer populates the enemy
                # board, so this only matters if something else ever does.
                st.board = None
            case ev.NextOpponent(player_id=pid):
                # A new opponent invalidates the previous one's odds well before
                # a fresh forecast arrives. Deliberately not gated on the
                # pipeline: stale odds must go regardless of whether the
                # opponent-memory lookup below can run.
                st.next_opponent_id = pid
                st.next_forecast = None
                self._next_odds = None
                self._refresh_hover_board()
            case ev.ShopBoard():
                # The number belongs to the exact friendly board submitted to
                # the simulator, so it must not caption the new board as if it
                # were its own. It used to be hidden until the re-run landed,
                # which blanked it on every buy, sell and reposition — while
                # the board was being arranged against it. Keep it, labelled.
                if self._next_odds:
                    st.next_forecast = f"{self._next_odds}  ·  recalculating…"
                    self._refresh_hover_board()
            case ev.ShopForecast(opponent_id=pid, seen_turn=seen_turn, turn=turn) \
                    if prediction is not None:
                # The derived event belongs to the pipeline's current opponent.
                # Accept it when restoring state before NextOpponent has been
                # seen, but never attach a late result to a newer opponent.
                if st.next_opponent_id is None:
                    st.next_opponent_id = pid
                if pid == st.next_opponent_id:
                    age = turn - seen_turn
                    staleness = (
                        "current" if age <= 0
                        else f"{age} turn{'s' if age > 1 else ''} old"
                    )
                    self._next_odds = _odds_line(prediction)
                    st.next_forecast = f"{self._next_odds}  ·  {staleness}"
                    self._refresh_hover_board()
            case ev.ShopForecast(opponent_id=pid) if prediction is None:
                if st.next_opponent_id is None:
                    st.next_opponent_id = pid
                if pid == st.next_opponent_id:
                    # A failed recompute must not leave the old number standing.
                    self._next_odds = None
                    st.next_forecast = "Odds unavailable for this board"
                    self._refresh_hover_board()
            case ev.GameEnd(placement=p):
                self._next_odds = None
                self._in_combat = False
                self._current_turn = None
                self._shop_result = (None, 0)
                self._clear_to_idle(st)
                st.phase = ("Game Over", f"finished #{p}" if p else "")
                st.status = f"Finished #{p}" if p else "Game over"
            case ev.RatingRead(rating=rating, delta=delta):
                st.status = f"MMR {rating}" + (f" ({delta:+d})" if delta is not None else "")
                self._hide_mmr_prompt()
            case ev.RatingMissed():
                # Asked only once the screen read has given up, so the nudge
                # never flashes up and is retracted a second later. Always
                # follows a GameEnd, with or without a placement — a finished
                # game is worth a rating either way.
                self._show_mmr_prompt()
        self._queue_flush()

    @staticmethod
    def _clear_forecast(st) -> None:
        """Blank the odds block. The inverse of `_set_forecast`."""
        st.odds = (None, None, None)
        st.damage = (None, None)
        st.lethal = None

    @classmethod
    def _clear_to_idle(cls, st) -> None:
        """Reset every field a game boundary blanks, leaving phase/status to
        the caller — the one thing GameStart and GameEnd disagree about."""
        st.turn = None
        st.combat = False
        cls._clear_forecast(st)
        st.result = (None, 0)
        st.forecast_live = True
        st.board = None
        st.next_opponent_id = None
        st.next_forecast = None
        st.standings = ()
        st.buffs = ((), (), 0, 0)
        st.hover_board = None

    @staticmethod
    def _enter_combat(st, snapshot) -> None:
        # A new fight drops the previous fight's result. The renderer only
        # pushes what changed, and HudPanel.set_odds hides the result widget as
        # a side effect, so without clearing it here two combats with the same
        # (outcome, damage) — back-to-back ties — would leave the second's
        # result caption hidden and never re-shown.
        st.result = (None, 0)
        st.phase = ("Combat Forecast", _combat_meta(snapshot))
        st.status = ""
        st.turn = snapshot.turn
        st.combat = True
        st.forecast_live = True
        # Deliberately no st.board: the game is showing this fight itself, so
        # the panel would only cover it. Reviewing a player's last-seen board
        # is the scout popout's job (hover_board).

    @staticmethod
    def _set_forecast(st, prediction: SimResult) -> None:
        st.odds = (
            prediction.won_percent, prediction.tied_percent, prediction.lost_percent
        )
        st.damage = (prediction.damage_dealt_text, prediction.damage_taken_text)
        st.lethal = prediction.lost_lethal_percent

    def _flush(self) -> None:
        """Push whatever changed since the last flush into the live window."""
        win = self.window
        if win is None:
            return
        render(win, self.state, self._rendered)
        self._rendered = self.state.snapshot()

    def _queue_flush(self) -> None:
        """Render once after a synchronous event burst has settled."""
        if not getattr(self, "_defer_flush", False):
            self._flush()
            return
        if self.window is None or self._flush_source is not None:
            return
        from gi.repository import GLib

        self._flush_source = GLib.idle_add(self._flush_idle)

    def _flush_idle(self) -> bool:
        self._flush_source = None
        self._flush()
        return False

    def run_with(self, coro: Coroutine) -> None:
        """Run the GTK app and the given coroutine on one shared loop."""
        import signal

        from gi.repository import GLib

        policy = GLibEventLoopPolicy()
        asyncio.set_event_loop_policy(policy)
        loop = policy.get_event_loop()
        task = loop.create_task(coro)
        task.add_done_callback(self._on_task_done)
        # __main__'s SIGTERM->KeyboardInterrupt handler covers the headless
        # asyncio.run() paths but not this one: while app.run blocks inside
        # GLib's C main loop, an exception raised by a Python signal handler
        # cannot unwind through it and is silently swallowed. steam-launch and
        # --replace both stop the tracker with SIGTERM, so without a GLib-level
        # handler the overlay — the mode steam-launch actually runs — ignores
        # its own shutdown signal (observed live: two SIGTERMs, still running).
        GLib.unix_signal_add(
            GLib.PRIORITY_HIGH, signal.SIGTERM, lambda: (self.app.quit(), False)[1]
        )
        try:
            self.app.run(None)
        finally:
            task.cancel()

    def _on_task_done(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            log.error("background task died", exc_info=task.exception())
            self.app.quit()
