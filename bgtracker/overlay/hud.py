"""The HUD panel: phase title, turn medallion, combat odds, damage forecast.

Top to bottom: a title row (phase name + a dim meta line), a hairline, then a
body row pairing the turn medallion with the win/tie/loss block and its
segmented bar, and finally the two damage pills. Outside combat the odds block
and pills hide and a plain status line takes their place.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import theme  # noqa: E402

MEDALLION_D = 58
BODY_SPACING = 12


def bar_widths(win: float, tie: float, loss: float, track: int) -> tuple[int, int, int]:
    """Split a track into win/tie/loss pixel widths that always sum to `track`.

    Rounding each share independently loses or gains a pixel and leaves a gap
    at the end of the bar, so the largest share absorbs the remainder.
    """
    total = win + tie + loss
    if total <= 0 or track <= 0:
        return (0, 0, 0)
    shares = [win / total, tie / total, loss / total]
    widths = [int(s * track) for s in shares]
    widths[shares.index(max(shares))] += track - sum(widths)
    return tuple(widths)


class HudPanel(Gtk.Box):
    def __init__(self, scale: float = 1.0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(8, scale))
        self.scale = scale
        # The panel is a fixed width, so the bar's track is exact arithmetic
        # rather than an expand-and-hope layout. The bar lives in the odds
        # column beside the medallion, so it gets what's left of the panel
        # after the padding, the medallion, and the gap between them.
        self.track = theme.px(
            theme.HUD_W - 2 * theme.PANEL_PAD_X - MEDALLION_D - BODY_SPACING, scale
        )

        # -- title row: phase name + meta ------------------------------
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=theme.px(8, scale))
        self.title = Gtk.Label(label="Waiting", xalign=0)
        self.title.add_css_class("title")
        self.meta = Gtk.Label(label="", xalign=1)
        self.meta.add_css_class("dim")
        self.meta.set_hexpand(True)
        title_row.append(self.title)
        title_row.append(self.meta)
        self.append(title_row)

        rule = Gtk.Box()
        rule.add_css_class("rule")
        self.append(rule)

        # -- status line (hero select / recruit) -----------------------
        self.status = Gtk.Label(label="waiting for game…", xalign=0)
        self.status.add_css_class("line")
        self.status.set_wrap(True)
        self.append(self.status)

        # -- body row: medallion + odds --------------------------------
        self.body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=theme.px(BODY_SPACING, scale))
        self.body.append(self._build_medallion(scale))
        self.body.append(self._build_odds(scale))
        self.append(self.body)

        # -- damage pills ----------------------------------------------
        self.pills = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=theme.px(6, scale))
        self.dealt = self._build_pill("dealt", "▲", "up")
        self.taken = self._build_pill("taken", "▼", "down")
        self.pills.append(self.dealt[0])
        self.pills.append(self.taken[0])
        self.append(self.pills)

        # Stands in for the forecast once the fight is over; hovering the HUD
        # swaps it back for the real thing.
        self.hint = Gtk.Label(label="▾ last fight", xalign=0)
        self.hint.add_css_class("odds-hint")
        self.append(self.hint)

        # Forecast display state. The numbers are kept after combat so they can
        # be recalled, but they only take up room while the fight is live or
        # while the pointer is over the HUD.
        self._odds: tuple[float, float, float] | None = None
        self._damage: tuple[float, float] | None = None
        self._live = False
        self._hovered = False

        self.set_turn(None)
        self.set_odds(None, None, None)

    # -- construction ---------------------------------------------------
    def _build_medallion(self, scale: float) -> Gtk.Widget:
        d = theme.px(MEDALLION_D, scale)
        self.medallion = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.medallion.add_css_class("medallion")
        self.medallion.set_size_request(d, d)
        self.medallion.set_valign(Gtk.Align.CENTER)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_vexpand(True)
        self.turn_num = Gtk.Label(label="–")
        self.turn_num.add_css_class("medallion-num")
        cap = Gtk.Label(label="TURN")
        cap.add_css_class("medallion-cap")
        inner.append(self.turn_num)
        inner.append(cap)
        self.medallion.append(inner)
        return self.medallion

    def _build_odds(self, scale: float) -> Gtk.Widget:
        self.odds_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(4, scale))
        self.odds_box.set_hexpand(True)

        numbers = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.odds_labels = {}
        for key, caption in (("win", "WIN"), ("tie", "TIE"), ("loss", "LOSS")):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(3, scale))
            value = Gtk.Label(label="–")
            value.add_css_class("odds-num")
            value.add_css_class(key)
            caption_label = Gtk.Label(label=caption)
            caption_label.add_css_class("odds-cap")
            column.append(value)
            column.append(caption_label)
            numbers.append(column)
            self.odds_labels[key] = value
        self.odds_box.append(numbers)

        self.bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.bar.add_css_class("bar-track")
        self.bar.set_overflow(Gtk.Overflow.HIDDEN)
        self.bar.set_size_request(self.track, theme.px(9, scale))
        self.bar_segments = {}
        for key in ("win", "tie", "loss"):
            segment = Gtk.Box()
            segment.add_css_class(f"bar-{key}")
            self.bar.append(segment)
            self.bar_segments[key] = segment
        self.odds_box.append(self.bar)
        return self.odds_box

    def _build_pill(self, kind: str, glyph: str, arrow: str) -> tuple[Gtk.Box, Gtk.Label]:
        pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=theme.px(4, self.scale))
        pill.add_css_class("pill")
        pill.add_css_class(kind)
        mark = Gtk.Label(label=glyph)
        mark.add_css_class("pill-arrow")
        mark.add_css_class(arrow)
        value = Gtk.Label(label="0")
        word = Gtk.Label(label=kind)
        word.add_css_class("pill-word")
        pill.append(mark)
        pill.append(value)
        pill.append(word)
        return pill, value

    # -- update API -----------------------------------------------------
    def set_phase(self, title: str, meta: str = "") -> None:
        self.title.set_label(title)
        self.meta.set_label(meta)

    def set_status(self, text: str) -> None:
        self.status.set_label(text)
        self.status.set_visible(bool(text))

    def set_turn(self, turn: int | None) -> None:
        self.turn_num.set_label("–" if turn is None else str(turn))
        self.medallion.set_visible(turn is not None)
        self._sync_body()

    def set_combat(self, combat: bool) -> None:
        """Gently pulse the medallion while a combat is being forecast."""
        if combat:
            self.medallion.add_css_class("combat")
        else:
            self.medallion.remove_css_class("combat")

    def set_odds(self, win: float | None, tie: float | None, loss: float | None) -> None:
        """Set the forecast. A fresh forecast is always shown expanded."""
        self._odds = None if win is None else (win, tie, loss)
        self._live = self._odds is not None
        if self._odds is None:
            self._damage = None
        else:
            for key, value in (("win", win), ("tie", tie), ("loss", loss)):
                self.odds_labels[key].set_label(f"{value:.0f}%")
            widths = bar_widths(win, tie, loss, self.track)
            for key, width in zip(("win", "tie", "loss"), widths):
                segment = self.bar_segments[key]
                segment.set_visible(width > 0)
                segment.set_size_request(width, -1)
        self._sync_odds()

    def set_damage(self, dealt: float | None, taken: float | None) -> None:
        self._damage = None if dealt is None else (dealt, taken)
        if self._damage is not None:
            self.dealt[1].set_label(f"{dealt:.0f}")
            self.taken[1].set_label(f"{taken:.0f}")
        self._sync_odds()

    def set_forecast_live(self, live: bool) -> None:
        """Whether the fight is still on. Once it isn't, the block collapses."""
        self._live = live
        self._sync_odds()

    def set_hovered(self, hovered: bool) -> None:
        """Pointer over the HUD — recall the last forecast while it is."""
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self._sync_odds()

    def _sync_odds(self) -> None:
        have = self._odds is not None
        expanded = have and (self._live or self._hovered)
        self.odds_box.set_visible(expanded)
        self.pills.set_visible(expanded and self._damage is not None)
        self.hint.set_visible(have and not expanded)
        self._sync_body()

    def _sync_body(self) -> None:
        self.body.set_visible(self.medallion.get_visible() or self.odds_box.get_visible())
