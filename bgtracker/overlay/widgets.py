"""Overlay widgets: HDT-style board rows with card tile art."""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from bgtracker.data import art, cards  # noqa: E402
from bgtracker.state.game import Minion, PlayerBoard  # noqa: E402

log = logging.getLogger(__name__)

TILE_W, TILE_H = 130, 34


class MinionRow(Gtk.Box):
    """One minion: tile art (when cached) + name + stats/keywords."""

    def __init__(self, minion: Minion):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.picture = Gtk.Picture()
        self.picture.set_size_request(TILE_W, TILE_H)
        self.picture.set_content_fit(Gtk.ContentFit.COVER)
        self.append(self.picture)

        kw = minion.flags
        label = Gtk.Label(
            label=f"{minion.attack}/{minion.health}{' ' + kw if kw else ''}",
            xalign=0,
        )
        label.add_css_class("stats")
        self.append(label)

        cached = art.cached_tile(minion.card_id or "")
        if cached:
            self.picture.set_filename(str(cached))
        else:
            # show name text until art lands
            self.name_label = Gtk.Label(label=cards.name(minion.card_id), xalign=0)
            self.name_label.add_css_class("line")
            self.insert_child_after(self.name_label, self.picture)
            self.picture.set_visible(False)
            if minion.card_id:
                asyncio.get_event_loop().create_task(self._load(minion.card_id))

    async def _load(self, card_id: str):
        path = await art.fetch_tile(card_id)
        if path is not None:
            self.picture.set_filename(str(path))
            self.picture.set_visible(True)
            self.name_label.set_visible(False)


class BoardPanel(Gtk.Box):
    """Vertical list of MinionRows for one player's board."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.header = Gtk.Label(label="", xalign=0)
        self.header.add_css_class("line")
        self.append(self.header)
        self._rows: list[MinionRow] = []

    def show_board(self, title: str, board: PlayerBoard | None):
        for row in self._rows:
            self.remove(row)
        self._rows.clear()
        if board is None:
            self.header.set_label(title)
            return
        self.header.set_label(
            f"{title} {cards.name(board.hero_card_id)} — "
            f"HP {board.health}+{board.armor}, tier {board.tier}"
        )
        for minion in board.minions:
            row = MinionRow(minion)
            self._rows.append(row)
            self.append(row)

    def clear(self):
        self.show_board("", None)
        self.header.set_label("")
