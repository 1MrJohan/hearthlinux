"""The leaderboard rail: eight hero orbs ranked down the left edge.

Each row is a hero portrait with its place badge and current HP. Your own row
carries a gold ring and reads YOU; eliminated players grey out and their HP is
struck through. Hovering a row highlights it and opens the scout popout — the
hover itself comes from `hover.py`'s pointer poll, so the rail never takes
pointer input and the overlay stays click-through.

Rows are built once and updated in place: standings now re-emit on every hero
HP change, and rebuilding would restart eight art fetches each tick.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from bgtracker.parse.events import Standing  # noqa: E402

from . import theme  # noqa: E402
from .widgets import ORB_FOCAL, ORB_ZOOM, RoundArt  # noqa: E402

SLOTS = 8


class _Row(Gtk.Box):
    def __init__(self, scale: float):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=theme.px(8, scale))
        self.add_css_class("rail-row")

        d = theme.px(theme.ORB_D, scale)
        badge = theme.px(17, scale)
        over = theme.px(4, scale)

        self.orb = RoundArt(d, css="orb", zoom=ORB_ZOOM, focal=ORB_FOCAL)
        self.rank = Gtk.Label(label="")
        self.rank.add_css_class("rank")
        self.rank.set_size_request(badge, badge)

        # Badge hangs off the orb's bottom-left corner; GTK4 forbids negative
        # margins, so a Fixed does the overhang.
        fixed = Gtk.Fixed()
        fixed.set_size_request(d + over, d + over)
        fixed.put(self.orb, over, 0)
        fixed.put(self.rank, 0, d + over - badge)
        self.append(fixed)

        self.hp = Gtk.Label(label="", xalign=1)
        self.hp.add_css_class("rail-hp")
        self.hp.set_hexpand(True)
        self.hp.set_valign(Gtk.Align.CENTER)
        self.append(self.hp)

    def update(self, standing: Standing | None) -> None:
        self.set_visible(standing is not None)
        if standing is None:
            return
        self.orb.load(standing.hero_card_id)
        self.rank.set_label(str(standing.place))
        self.hp.set_label("YOU" if standing.you else str(standing.total_health))
        for widget, classes in (
            (self.orb, ("you", "dead")),
            (self.rank, ("you",)),
            (self.hp, ("you", "dead")),
        ):
            for name in classes:
                widget.remove_css_class(name)
        if standing.you:
            self.orb.add_css_class("you")
            self.rank.add_css_class("you")
            self.hp.add_css_class("you")
        if standing.dead:
            self.orb.add_css_class("dead")
            self.hp.add_css_class("dead")


class LeaderboardRail(Gtk.Box):
    def __init__(self, scale: float = 1.0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(5, scale))
        self.set_size_request(theme.px(theme.RAIL_W, scale), -1)
        self._rows = [_Row(scale) for _ in range(SLOTS)]
        for row in self._rows:
            row.set_visible(False)
            self.append(row)
        self._places: list[int] = []

    def set_standings(self, standings: tuple[Standing, ...]) -> None:
        ordered = sorted(standings, key=lambda s: s.place)[:SLOTS]
        for row, standing in zip(self._rows, ordered):
            row.update(standing)
        for row in self._rows[len(ordered):]:
            row.update(None)
        self._places = [s.place for s in ordered]

    def set_hot(self, place: int | None) -> None:
        """Highlight the row for `place` (1-based), or none."""
        for index, row in enumerate(self._rows):
            hot = place is not None and index < len(self._places) and self._places[index] == place
            if hot:
                row.add_css_class("hot")
            else:
                row.remove_css_class("hot")

    @property
    def rows(self) -> int:
        return len(self._places)
