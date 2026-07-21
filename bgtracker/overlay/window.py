"""Transparent, click-through layer-shell overlay window (KWin Wayland)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

CSS = b"""
.hud {
    background-color: rgba(15, 18, 24, 0.82);
    color: #e8e8e8;
    border-radius: 10px;
    padding: 12px 16px;
}
.hud .odds { font-size: 22px; font-weight: bold; }
.hud .win  { color: #6fd66f; }
.hud .tie  { color: #d6c96f; }
.hud .loss { color: #d66f6f; }
.hud .dim  { color: #9a9a9a; font-size: 12px; }
.hud .line { font-size: 13px; }
"""


class OverlayWindow(Gtk.Window):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application)
        LayerShell.init_for_window(self)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
        LayerShell.set_margin(self, LayerShell.Edge.TOP, 60)
        LayerShell.set_margin(self, LayerShell.Edge.RIGHT, 30)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("hud")

        self.status = Gtk.Label(label="waiting for game…", xalign=0)
        self.status.add_css_class("dim")
        self.odds = Gtk.Label(label="", xalign=0)
        self.odds.add_css_class("odds")
        self.board = Gtk.Label(label="", xalign=0, wrap=True)
        self.board.add_css_class("line")
        self.memory = Gtk.Label(label="", xalign=0, wrap=True)
        self.memory.add_css_class("dim")
        for w in (self.status, self.odds, self.board, self.memory):
            box.append(w)
        self.set_child(box)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
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
            return
        self.odds.set_markup(
            f'<span foreground="#6fd66f">{win:.0f}%</span> / '
            f'<span foreground="#d6c96f">{tie:.0f}%</span> / '
            f'<span foreground="#d66f6f">{loss:.0f}%</span>'
        )

    def set_board(self, text: str) -> None:
        self.board.set_label(text)

    def set_memory(self, text: str) -> None:
        self.memory.set_label(text)
