"""Transparent, click-through layer-shell overlay window (KWin Wayland)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

from bgtracker.config import Config  # noqa: E402

from .widgets import BoardPanel  # noqa: E402

CSS_TEMPLATE = """
.hud {{
    background-color: rgba(15, 18, 24, 0.82);
    color: #e8e8e8;
    border-radius: 10px;
    padding: {pad}px {pad2}px;
}}
.hud .odds {{ font-size: {odds_px}px; font-weight: bold; }}
.hud .dim  {{ color: #9a9a9a; font-size: {dim_px}px; }}
.hud .line {{ font-size: {line_px}px; }}
.hud .stats {{ font-size: {line_px}px; font-weight: bold; }}
"""

_ANCHORS = {
    "top-right": (LayerShell.Edge.TOP, LayerShell.Edge.RIGHT),
    "top-left": (LayerShell.Edge.TOP, LayerShell.Edge.LEFT),
    "bottom-right": (LayerShell.Edge.BOTTOM, LayerShell.Edge.RIGHT),
    "bottom-left": (LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT),
}


class OverlayWindow(Gtk.Window):
    def __init__(self, application: Gtk.Application, cfg: Config | None = None):
        super().__init__(application=application)
        cfg = cfg or Config()
        scale = max(0.5, min(3.0, cfg.overlay_scale))
        corner = cfg.extra.get("overlay_corner", "top-right")

        LayerShell.init_for_window(self)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        for edge in _ANCHORS.get(corner, _ANCHORS["top-right"]):
            LayerShell.set_anchor(self, edge, True)
            LayerShell.set_margin(self, edge, int(40 * scale))
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("hud")

        self.status = Gtk.Label(label="waiting for game…", xalign=0)
        self.status.add_css_class("dim")
        self.odds = Gtk.Label(label="", xalign=0)
        self.odds.add_css_class("odds")
        self.damage = Gtk.Label(label="", xalign=0)
        self.damage.add_css_class("dim")
        self.board = BoardPanel()
        self.memory = Gtk.Label(label="", xalign=0, wrap=True)
        self.memory.add_css_class("dim")
        for w in (self.status, self.odds, self.damage, self.board, self.memory):
            box.append(w)
        self.set_child(box)

        provider = Gtk.CssProvider()
        provider.load_from_data(
            CSS_TEMPLATE.format(
                pad=int(12 * scale),
                pad2=int(16 * scale),
                odds_px=int(22 * scale),
                dim_px=int(12 * scale),
                line_px=int(13 * scale),
            ).encode()
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.connect("realize", self._make_click_through)

    def _make_click_through(self, *_):
        surface = self.get_surface()
        if surface is not None:
            import cairo

            surface.set_input_region(cairo.Region())

    # -- update API (call from the GLib/asyncio loop) -------------------
    def set_status(self, text: str) -> None:
        self.status.set_label(text)

    def set_odds(self, win: float | None, tie: float | None, loss: float | None) -> None:
        if win is None:
            self.odds.set_label("")
            self.damage.set_label("")
            return
        self.odds.set_markup(
            f'<span foreground="#6fd66f">{win:.0f}%</span> / '
            f'<span foreground="#d6c96f">{tie:.0f}%</span> / '
            f'<span foreground="#d66f6f">{loss:.0f}%</span>'
        )

    def set_damage(self, dealt: float | None, taken: float | None) -> None:
        if dealt is None:
            self.damage.set_label("")
        else:
            self.damage.set_label(f"dmg dealt ~{dealt:.0f} / taken ~{taken:.0f}")

    def set_board(self, title: str, board) -> None:
        self.board.show_board(title, board)

    def clear_board(self) -> None:
        self.board.clear()

    def set_memory(self, text: str) -> None:
        self.memory.set_label(text)
