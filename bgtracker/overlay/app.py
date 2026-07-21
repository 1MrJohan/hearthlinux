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

from bgtracker.headless import render_board_line  # noqa: E402
from bgtracker.data import cards  # noqa: E402
from bgtracker.parse import events as ev  # noqa: E402
from bgtracker.sim.client import SimResult  # noqa: E402

from .window import OverlayWindow  # noqa: E402

log = logging.getLogger(__name__)


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
        if slot is None or slot >= len(self.standings):
            win.clear_hover_board()
            return
        place, player_id, hero_card_id = self.standings[slot]
        seen = self.pipeline.memory.last_seen(player_id) if self.pipeline else None
        if seen:
            win.set_hover_board(
                f"#{place} {cards.name(hero_card_id)} — last seen turn {seen.turn}",
                seen.board,
            )
        else:
            win.set_hover_board(f"#{place} {cards.name(hero_card_id)} — not fought yet", None)

    def _on_activate(self, app):
        from bgtracker.config import load_config

        cfg = load_config()
        self.window = OverlayWindow(application=app, cfg=cfg)
        self.window.present()
        if cfg.extra.get("hover_strips", True):
            from .hover import HoverStrips

            self.hover = HoverStrips(application=app, cfg=cfg, on_slot=self._on_hover_slot)
            self.hover.present()

    # Pipeline listener -------------------------------------------------
    def on_event(self, event: ev.Event, prediction: SimResult | None) -> None:
        win = self.window
        if win is None:
            return
        match event:
            case ev.GameStart():
                win.set_status("game started — pick a hero")
                win.set_odds(None, None, None)
                win.clear_board()
                win.set_memory("")
            case ev.HeroPicked(card_id=cid):
                win.set_status(f"playing {cards.name(cid)}")
            case ev.TurnChange(turn=t):
                win.set_status(f"turn {t}")
            case ev.CombatStart(snapshot=s):
                win.set_status(f"combat — turn {s.turn}")
                if prediction is not None:
                    win.set_odds(
                        prediction.won_percent,
                        prediction.tied_percent,
                        prediction.lost_percent,
                    )
                    win.set_damage(prediction.avg_damage_won, prediction.avg_damage_lost)
                else:
                    win.set_odds(None, None, None)
                win.set_board("vs", s.opponent)
            case ev.CombatEnd(snapshot=s):
                you = s.friendly
                if you:
                    win.set_status(f"shopping — HP {you.health + you.armor}, tier {you.tier}")
            case ev.NextOpponent(player_id=pid) if self.pipeline is not None:
                seen = self.pipeline.memory.last_seen(pid)
                if seen and seen.board:
                    win.set_memory(
                        f"next: {cards.name(seen.board.hero_card_id)} "
                        f"(turn {seen.turn}): {render_board_line(seen.board)}"
                    )
                else:
                    win.set_memory("next opponent: not seen yet")
            case ev.Standings(places=places):
                self.standings = places
            case ev.GameEnd(placement=p):
                win.set_status(f"finished #{p}" if p else "game over")
                win.set_odds(None, None, None)
                win.clear_board()
                win.clear_hover_board()
                self.standings = ()
                win.set_memory("")

    def show_memory(self, text: str) -> None:
        if self.window:
            self.window.set_memory(text)

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
