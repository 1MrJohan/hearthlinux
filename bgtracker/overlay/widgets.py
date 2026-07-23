"""Overlay widgets: a player's board as a horizontal row of full card images.

Each minion shows its rendered card plus the LIVE (buffed) attack/health and
keywords underneath — the printed card only carries base stats, which in
Battlegrounds are almost never the real ones.
"""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from bgtracker.data import art, cards  # noqa: E402
from bgtracker.state.game import Minion, PlayerBoard  # noqa: E402

log = logging.getLogger(__name__)

CARD_W, CARD_H = 88, 132


class MinionCard(Gtk.Box):
    """One minion: full card render on top, live stats/keywords beneath."""

    def __init__(self, minion: Minion):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.set_size_request(CARD_W, -1)

        self.picture = Gtk.Picture()
        self.picture.set_size_request(CARD_W, CARD_H)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.append(self.picture)

        kw = minion.flags
        stats = Gtk.Label(
            label=f"{minion.attack}/{minion.health}{' ' + kw if kw else ''}",
            xalign=0.5,
        )
        stats.add_css_class("stats")
        self.append(stats)

        self.name_label: Gtk.Label | None = None
        cached = art.cached_card(minion.card_id or "")
        if cached:
            self.picture.set_filename(str(cached))
        else:
            # Show the name in the card's place until the render arrives.
            self.name_label = Gtk.Label(
                label=cards.name(minion.card_id), xalign=0.5,
                wrap=True, max_width_chars=11,
            )
            self.name_label.add_css_class("line")
            self.name_label.set_size_request(CARD_W, CARD_H)
            self.insert_child_after(self.name_label, self.picture)
            self.picture.set_visible(False)
            if minion.card_id:
                asyncio.get_event_loop().create_task(self._load(minion.card_id))

    async def _load(self, card_id: str):
        path = await art.fetch_card(card_id)
        if path is not None:
            self.picture.set_filename(str(path))
            self.picture.set_visible(True)
            if self.name_label is not None:
                self.name_label.set_visible(False)


class BoardPanel(Gtk.Box):
    """Header line above a horizontal row of MinionCards for one board."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.header = Gtk.Label(label="", xalign=0)
        self.header.add_css_class("line")
        self.append(self.header)
        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.append(self.row)
        self._cards: list[MinionCard] = []

    def show_board(self, title: str, board: PlayerBoard | None):
        for card in self._cards:
            self.row.remove(card)
        self._cards.clear()
        if board is None:
            self.header.set_label(title)
            return
        self.header.set_label(
            f"{title} {cards.name(board.hero_card_id)} — "
            f"HP {board.health}+{board.armor}, tier {board.tier}"
        )
        for minion in board.minions:
            card = MinionCard(minion)
            self._cards.append(card)
            self.row.append(card)

    def clear(self):
        self.show_board("", None)
        self.header.set_label("")
