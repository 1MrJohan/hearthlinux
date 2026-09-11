"""Overlay board widgets: circular art, minion tiles, and a board panel.

A minion tile is an oak plaque holding a circular crop of the card's
illustration, an attack and a health gem, the name, and one pip per keyword.
Stats come from the LIVE (buffed) values — the printed card only carries base
stats, which in Battlegrounds are almost never the real ones.
"""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

from bgtracker.data import art, cards  # noqa: E402
from bgtracker.state.game import Minion, PlayerBoard  # noqa: E402

from . import theme  # noqa: E402

log = logging.getLogger(__name__)

# The design crops a square illustration into a circle: scale it up and slide
# it up so the character's face, not their chest, fills the disc.
PORTRAIT_ZOOM, PORTRAIT_FOCAL = 1.5, 0.22
ORB_ZOOM, ORB_FOCAL = 1.25, 0.12


def pip_classes(flags: str) -> list[str]:
    """CSS classes for a minion's keyword pips, in Minion.flags order.

    "G" (golden) is excluded — it is the tile's border treatment, not a pip —
    as is any letter the palette has no colour for.
    """
    return [f"pip-{f.lower()}" for f in flags if f in theme.PIP_COLOURS]


def magnet_pip(minion: Minion) -> str | None:
    """Label for the magnetized-cards pip, or None when nothing is magnetized.

    "M" for one linked card, "M2" for two, and so on. This is a count of
    distinct *cards*, not of mechs — the log folds repeats of the same card
    into one enchantment, and the total is not recoverable (see
    docs/superpowers/specs/2026-08-29-magnetize-count-design.md). The number
    is exact for the opponent too, so the scout popout shows it.
    """
    n = minion.linked_cards
    if n <= 0:
        return None
    return "M" if n == 1 else f"M{n}"


class RoundArt(Gtk.Widget):
    """A square art crop clipped to a circle, zoomed and shifted to frame a face.

    Drawn rather than composed: a `Gtk.Picture` reports the texture's intrinsic
    size (256px) as its natural size, which every container honours — that
    inflates the tile and over-zooms the crop. Snapshotting the texture
    directly pins the widget to `diameter` and makes the zoom/focal transform
    exact. The ring, inner shadow and grayscale-when-dead still come from CSS:
    GTK renders a widget's CSS box before invoking this vfunc.
    """

    def __init__(self, diameter: int, css: str = "portrait",
                 zoom: float = PORTRAIT_ZOOM, focal: float = PORTRAIT_FOCAL):
        super().__init__()
        self.add_css_class(css)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self._diameter = diameter
        self._zoom = zoom
        self._focal = focal
        self._texture: Gdk.Texture | None = None
        self._card_id: str | None = None
        self._task: asyncio.Task | None = None

    def do_measure(self, _orientation, _for_size):
        return (self._diameter, self._diameter, -1, -1)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if self._texture is None or width <= 0 or height <= 0:
            return
        bounds = Graphene.Rect().init(0, 0, width, height)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(bounds, min(width, height) / 2)
        snapshot.push_rounded_clip(clip)
        side = max(width, height) * self._zoom
        snapshot.append_texture(
            self._texture,
            Graphene.Rect().init((width - side) / 2, -height * self._focal, side, side),
        )
        snapshot.pop()

    def load(self, card_id: str | None) -> None:
        """Show the card's art crop, fetching it in the background on a miss.

        A no-op when the art is already showing, so callers that refresh often
        (the rail redraws on every HP tick) don't re-trigger fetches.
        """
        if not card_id or card_id == self._card_id:
            return
        self._card_id = card_id
        cached = art.cached_art(card_id, "crop")
        if cached:
            self._set_texture(cached)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (tests / headless): stay on the gradient backdrop
        self._task = loop.create_task(self._fetch(card_id))

    async def _fetch(self, card_id: str) -> None:
        path = await art.fetch_art(card_id, "crop")
        if path is not None:
            self._set_texture(path)

    def _set_texture(self, path) -> None:
        try:
            self._texture = Gdk.Texture.new_from_filename(str(path))
        except Exception as exc:
            log.debug("could not load art %s: %s", path, exc)
            return
        self.queue_draw()


class MinionTile(Gtk.Box):
    """One minion: circular portrait with stat gems, name, and keyword pips."""

    def __init__(self, minion: Minion, scale: float = 1.0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(3, scale))
        self.add_css_class("tile")
        if minion.golden:
            self.add_css_class("golden")
        self.set_size_request(theme.px(theme.TILE_W, scale), -1)

        self.append(self._portrait_with_gems(minion, scale))

        name = Gtk.Label(label=cards.name(minion.card_id))
        name.add_css_class("tile-name")
        name.set_wrap(True)
        name.set_lines(2)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_justify(Gtk.Justification.CENTER)
        name.set_max_width_chars(12)
        self.append(name)

        labelled = list(zip(
            (f for f in minion.flags if f in theme.PIP_COLOURS),
            pip_classes(minion.flags),
        ))
        magnet = magnet_pip(minion)
        if magnet:
            labelled.append((magnet, "pip-m"))
        if labelled:
            pips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=theme.px(2, scale))
            pips.set_halign(Gtk.Align.CENTER)
            for text, css in labelled:
                pip = Gtk.Label(label=text)
                pip.add_css_class("pip")
                pip.add_css_class(css)
                pips.append(pip)
            self.append(pips)

    def _portrait_with_gems(self, minion: Minion, scale: float) -> Gtk.Widget:
        """Portrait disc with the two gems overhanging its bottom corners.

        A Gtk.Fixed rather than a Gtk.Overlay: the design hangs the gems 5px
        outside the disc and 4px below it, and GTK4 rejects negative margins.
        """
        d = theme.px(theme.PORTRAIT_D, scale)
        gem = theme.px(25, scale)
        over = theme.px(5, scale)      # gem overhang left/right of the disc
        drop = theme.px(4, scale)      # gem drop below the disc

        width, height = d + 2 * over, d + drop
        fixed = Gtk.Fixed()
        fixed.set_size_request(width, height)
        fixed.set_halign(Gtk.Align.CENTER)

        portrait = RoundArt(d)
        portrait.load(minion.card_id)
        fixed.put(portrait, over, 0)

        gem_y = height - gem
        fixed.put(self._gem(minion.attack, "atk", gem), 0, gem_y)
        fixed.put(self._gem(minion.health, "hp", gem), width - gem, gem_y)
        return fixed

    @staticmethod
    def _gem(value: int, kind: str, size: int) -> Gtk.Label:
        label = Gtk.Label(label=str(value))
        label.add_css_class("gem")
        label.add_css_class(kind)
        label.set_size_request(size, size)
        return label


class BoardPanel(Gtk.Box):
    """A titled header over a horizontal row of minion tiles.

    `show_orb=True` prefixes the header with the player's hero portrait — the
    scout popout uses it; the enemy/next board panels do not.
    """

    def __init__(self, show_orb: bool = False, scale: float = 1.0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,
                         spacing=theme.px(8, scale))
        self.scale = scale

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                         spacing=theme.px(10, scale))
        self.orb: RoundArt | None = None
        if show_orb:
            self.orb = RoundArt(
                theme.px(theme.HOVER_ORB_D, scale), css="orb",
                zoom=ORB_ZOOM, focal=ORB_FOCAL,
            )
            header.append(self.orb)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(3, scale))
        titles.set_valign(Gtk.Align.CENTER)
        self.title = Gtk.Label(label="", xalign=0)
        self.title.add_css_class("title")
        self.subtitle = Gtk.Label(label="", xalign=0)
        self.subtitle.add_css_class("dim")
        titles.append(self.title)
        titles.append(self.subtitle)
        header.append(titles)
        self.append(header)

        self.row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=theme.px(6, scale))
        self.row.set_halign(Gtk.Align.CENTER)
        self.append(self.row)
        self._tiles: list[MinionTile] = []

        # Odds against this board, for panels that forecast one. Sits under the
        # tiles so the number stays next to the board it was derived from.
        self.odds = Gtk.Label(label="", xalign=0)
        self.odds.add_css_class("board-odds")
        self.odds.set_visible(False)
        self.append(self.odds)

    def set_odds(self, text: str | None) -> None:
        """A one-line forecast under the board, or None to hide it."""
        self.odds.set_label(text or "")
        self.odds.set_visible(bool(text))

    def show_board(
        self,
        title: str,
        subtitle: str = "",
        board: PlayerBoard | None = None,
        hero_card_id: str | None = None,
        dead: bool = False,
    ) -> None:
        for tile in self._tiles:
            self.row.remove(tile)
        self._tiles.clear()

        self.title.set_label(title)
        self.subtitle.set_label(subtitle)
        self.subtitle.set_visible(bool(subtitle))

        if self.orb is not None:
            hero = hero_card_id or (board.hero_card_id if board else None)
            self.orb.set_visible(bool(hero))
            if hero:
                self.orb.load(hero)
            self.orb.remove_css_class("dead")
            if dead:
                self.orb.add_css_class("dead")

        if board is None:
            return
        for minion in board.minions:
            tile = MinionTile(minion, self.scale)
            self._tiles.append(tile)
            self.row.append(tile)

    def clear(self) -> None:
        self.show_board("")
        self.set_odds(None)
