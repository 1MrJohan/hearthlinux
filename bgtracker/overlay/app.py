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

from bgtracker.headless import render_board_line, render_minion  # noqa: E402
from bgtracker.data import cards  # noqa: E402
from bgtracker.parse import events as ev  # noqa: E402
from bgtracker.sim.client import SimResult  # noqa: E402

from .window import OverlayWindow  # noqa: E402

log = logging.getLogger(__name__)


class OverlayApp:
    """Owns the Gtk.Application + window; listener plugs into the Pipeline."""

    def __init__(self):
        self.app = Gtk.Application(application_id="dev.bgtracker.overlay")
        self.window: OverlayWindow | None = None
        self.pipeline = None  # set by the caller for opponent-memory lookups
        self.app.connect("activate", self._on_activate)

    def _on_activate(self, app):
        self.window = OverlayWindow(application=app)
        self.window.present()

    # Pipeline listener -------------------------------------------------
    def on_event(self, event: ev.Event, prediction: SimResult | None) -> None:
        win = self.window
        if win is None:
            return
        match event:
            case ev.GameStart():
                win.set_status("game started — pick a hero")
                win.set_odds(None, None, None)
                win.set_board("")
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
                else:
                    win.set_odds(None, None, None)
                if s.opponent is not None:
                    win.set_board(
                        f"vs {cards.name(s.opponent.hero_card_id)}: "
                        + ", ".join(render_minion(m) for m in s.opponent.minions)
                    )
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
            case ev.GameEnd(placement=p):
                win.set_status(f"finished #{p}" if p else "game over")
                win.set_odds(None, None, None)
                win.set_board("")
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
