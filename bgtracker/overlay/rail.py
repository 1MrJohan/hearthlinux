"""The leaderboard rail: eight hero orbs ranked down the left edge.

Each row is a hero portrait with its place badge, current HP, and tavern tier.
Your own row carries a gold ring while retaining its numeric HP; eliminated
players grey out and their HP is struck through. Hovering a row highlights it
and opens the scout popout — the hover itself comes from `hover.py`'s pointer
poll, so the rail never takes pointer input and the overlay stays click-through.

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


def status_labels(standing: Standing) -> tuple[str, str]:
    """Effective health and an honest compact tavern-tier label."""
    tier = standing.tier
    tier_text = f"T{tier}" if isinstance(tier, int) and 1 <= tier <= 6 else "T—"
    return str(standing.total_health), tier_text


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

        status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        status.set_hexpand(True)
        status.set_valign(Gtk.Align.CENTER)

        self.hp = Gtk.Label(label="", xalign=1)
        self.hp.add_css_class("rail-hp")
        self.hp.set_hexpand(True)
        status.append(self.hp)

        self.tier = Gtk.Label(label="", xalign=1)
        self.tier.add_css_class("rail-tier")
        self.tier.set_hexpand(True)
        status.append(self.tier)
        self.append(status)

    player_id: int | None = None
    dead: bool = False

    def update(self, standing: Standing | None) -> None:
        self.set_visible(standing is not None)
        self.player_id = standing.player_id if standing else None
        self.dead = bool(standing and standing.dead)
        if standing is None:
            return
        self.orb.load(standing.hero_card_id)
        self.rank.set_label(str(standing.place))
        health, tier = status_labels(standing)
        self.hp.set_label(health)
        self.tier.set_label(tier)
        for widget, classes in (
            (self.orb, ("you", "dead")),
            (self.rank, ("you",)),
            (self.hp, ("you", "dead")),
            (self.tier, ("you", "dead")),
        ):
            for name in classes:
                widget.remove_css_class(name)
        if standing.you:
            self.orb.add_css_class("you")
            self.rank.add_css_class("you")
            self.hp.add_css_class("you")
            self.tier.add_css_class("you")
        if standing.dead:
            self.orb.add_css_class("dead")
            self.hp.add_css_class("dead")
            self.tier.add_css_class("dead")

    def set_next(self, is_next: bool) -> None:
        if is_next:
            self.orb.add_css_class("next")
        else:
            self.orb.remove_css_class("next")


class LeaderboardRail(Gtk.Box):
    def __init__(self, scale: float = 1.0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(5, scale))
        self.set_size_request(theme.px(theme.RAIL_W, scale), -1)
        self._rows = [_Row(scale) for _ in range(SLOTS)]
        for row in self._rows:
            row.set_visible(False)
            self.append(row)
        self._places: list[int] = []
        self._next: int | None = None

    def set_standings(self, standings: tuple[Standing, ...]) -> None:
        ordered = sorted(standings, key=lambda s: s.place)[:SLOTS]
        for row, standing in zip(self._rows, ordered):
            row.update(standing)
        for row in self._rows[len(ordered):]:
            row.update(None)
        self._places = [s.place for s in ordered]
        # Rows are keyed by place, so a reorder moves the opponent to another
        # row; re-resolve the marker against PLAYER_ID every time.
        self._apply_next()

    def set_next_opponent(self, player_id: int | None) -> None:
        """Ring the row of the player you fight next (PLAYER_ID), or none."""
        self._next = player_id
        self._apply_next()

    def _apply_next(self) -> None:
        for row in self._rows:
            row.set_next(
                self._next is not None and row.player_id == self._next and not row.dead
            )

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
