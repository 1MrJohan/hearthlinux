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

from .window import OverlayWindow  # noqa: E402

log = logging.getLogger(__name__)


def _hero_meta(board) -> str:
    """`18 HP · Tavern 3` — the dim line beside the phase title."""
    if board is None:
        return ""
    return f"{board.health + board.armor} HP · Tavern {board.tier}"


def _combat_meta(snapshot) -> str:
    """`18 HP · vs Tickatus` — your HP and who you are up against."""
    you, opponent = snapshot.friendly, snapshot.opponent
    hp = f"{you.health + you.armor} HP" if you else ""
    versus = f"vs {cards.name(opponent.hero_card_id)}" if opponent else ""
    return " · ".join(part for part in (hp, versus) if part)


def _board_meta(board) -> str:
    """`Tickatus · 27 HP · Tavern 5` — the enemy board's subtitle."""
    if board is None:
        return ""
    return (
        f"{cards.name(board.hero_card_id)} · "
        f"{board.health + board.armor} HP · Tavern {board.tier}"
    )


class OverlayApp:
    """Owns the Gtk.Application + window; listener plugs into the Pipeline."""

    def __init__(self):
        from gi.repository import Gio

        # NON_UNIQUE: a stale instance must never make a new launch silently
        # defer to it and exit.
        self.app = Gtk.Application(
            application_id="dev.bgtracker.overlay",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.window: OverlayWindow | None = None
        self.hover = None
        self.pipeline = None  # set by the caller for opponent-memory lookups
        self.standings: tuple = ()
        self.app.connect("activate", self._on_activate)

    def _on_hover_slot(self, slot: int | None) -> None:
        win = self.window
        if win is None:
            return
        # Slot i is leaderboard position i+1; match by place, not list index.
        entry = next((e for e in self.standings if e.place == (slot + 1)), None) if slot is not None else None
        win.set_hot_place(entry.place if entry else None)
        if entry is None:
            if slot is not None:
                log.info("hover-lookup: slot %s -> no standings entry (standings=%s)", slot, self.standings)
            win.clear_hover_board()
            return
        seen = self.pipeline.memory.last_seen(entry.player_id) if self.pipeline else None
        log.info(
            "hover-lookup: slot %s -> place %s %s pid=%s seen=%s",
            slot, entry.place, entry.hero_card_id, entry.player_id,
            f"turn {seen.turn}" if seen else None,
        )
        if entry.dead:
            status = "eliminated"
        elif seen:
            status = f"last seen · turn {seen.turn}"
        elif entry.you:
            status = "this is you"
        else:
            status = "not scouted yet"
        win.set_hover_board(
            f"#{entry.place} {cards.name(entry.hero_card_id)}",
            status,
            seen.board if seen else None,
            hero_card_id=entry.hero_card_id,
            dead=entry.dead,
        )

    def _on_activate(self, app):
        from bgtracker.config import load_config

        from . import theme

        # Must precede any widget construction: Pango caches the face it picks
        # for a description, so one lookup before registration would pin the
        # whole overlay to the fallback font for the process's lifetime.
        theme.register_fonts()
        cfg = load_config()
        self.window = OverlayWindow(application=app, cfg=cfg)
        self.window.present()
        if cfg.extra.get("hover_strips", True):
            from .hover import HoverStrips

            self.hover = HoverStrips(
                application=app, cfg=cfg, on_slot=self._on_hover_slot,
                rail_rect=self.window.rail_rect,
                hud_rect=self.window.hud_rect,
                on_hud=self._on_hud_hover,
            )

    def _on_hud_hover(self, hovered: bool) -> None:
        if self.window is not None:
            self.window.set_hud_hovered(hovered)
            self.hover.present()

    # Pipeline listener -------------------------------------------------
    def on_event(self, event: ev.Event, prediction: SimResult | None) -> None:
        # State updates must happen even before the window exists — events
        # streamed during startup replay would otherwise be lost.
        if isinstance(event, ev.Standings):
            self.standings = event.places
        elif isinstance(event, ev.GameStart):
            self.standings = ()
        win = self.window
        if win is None:
            return
        match event:
            case ev.GameStart():
                win.set_phase("Hero Select")
                win.set_status("Waiting — choose your hero")
                win.set_turn(None)
                win.set_combat(False)
                win.set_odds(None, None, None)
                win.clear_board()
                win.clear_next_board()
                win.set_standings(())
                win.set_buffs(())
            case ev.HeroPicked(card_id=cid):
                win.set_status(f"Playing {cards.name(cid)}")
            case ev.TurnChange(turn=t):
                win.set_turn(t)
            case ev.CombatStart(snapshot=s):
                win.set_phase("Combat Forecast", _combat_meta(s))
                win.set_status("")
                win.set_turn(s.turn)
                win.set_combat(True)
                if prediction is not None:
                    win.set_odds(
                        prediction.won_percent,
                        prediction.tied_percent,
                        prediction.lost_percent,
                    )
                    win.set_damage(prediction.avg_damage_won, prediction.avg_damage_lost)
                else:
                    win.set_odds(None, None, None)
                win.set_board("Enemy Board", _board_meta(s.opponent), s.opponent)
            case ev.Buffs(entries=e, spells=sp):
                win.set_buffs(e, sp)
            case ev.Standings(places=places):
                win.set_standings(places)
            case ev.CombatEnd(snapshot=s):
                you = s.friendly
                win.set_phase("Recruit Phase", _hero_meta(you))
                win.set_status("")
                win.set_combat(False)
                # The forecast is kept, not discarded: the fight resolves in
                # seconds and the sim is awaited before the overlay hears about
                # it at all, so it collapses to a hint here and comes back when
                # the pointer is over the HUD. The next CombatStart replaces it.
                win.set_forecast_live(False)
                # The design shows the enemy board only during combat.
                win.clear_board()
            case ev.NextOpponent(player_id=pid) if self.pipeline is not None:
                seen = self.pipeline.memory.last_seen(pid)
                if seen and seen.board:
                    win.set_next_board(
                        "Next Opponent", f"last seen · turn {seen.turn}", seen.board
                    )
                else:
                    win.set_next_board("Next Opponent", "not scouted yet", None)
            case ev.GameEnd(placement=p):
                win.set_phase("Game Over", f"finished #{p}" if p else "")
                win.set_status(f"Finished #{p}" if p else "Game over")
                win.set_turn(None)
                win.set_combat(False)
                win.set_odds(None, None, None)
                win.clear_board()
                win.clear_hover_board()
                self.standings = ()
                win.set_standings(())
                win.clear_next_board()
                win.set_buffs(())

    def run_with(self, coro: Coroutine) -> None:
        """Run the GTK app and the given coroutine on one shared loop."""
        policy = GLibEventLoopPolicy()
        asyncio.set_event_loop_policy(policy)
        loop = policy.get_event_loop()
        task = loop.create_task(coro)
        task.add_done_callback(self._on_task_done)
        try:
            self.app.run(None)
        finally:
            task.cancel()

    def _on_task_done(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            log.error("background task died", exc_info=task.exception())
            self.app.quit()
