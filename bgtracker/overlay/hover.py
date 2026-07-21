"""Invisible hover strips over the BG leaderboard portraits.

A thin layer-shell column sits on the left screen edge where the in-game
leaderboard lives. Unlike the main overlay it ACCEPTS pointer input in that
band: hovering slot N shows that player's last-seen board. Trade-off: the
game's native hover tooltip won't fire while the pointer is on the strip
(toggle with hover_strips=false in config.toml).

Geometry defaults assume fullscreen 16:9; tune leaderboard_top_frac /
leaderboard_bottom_frac / leaderboard_width_px in config.toml if the strips
don't line up with the portraits.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

from bgtracker.config import Config  # noqa: E402

log = logging.getLogger(__name__)


class HoverStrips(Gtk.Window):
    """on_slot(index | None) fires as the pointer enters/leaves portrait rows."""

    def __init__(
        self,
        application: Gtk.Application,
        cfg: Config,
        on_slot: Callable[[int | None], None],
        slots: int = 8,
    ):
        super().__init__(application=application)
        self.on_slot = on_slot
        self.slots = slots
        self._current: int | None = None

        extra = cfg.extra
        self.top_frac = float(extra.get("leaderboard_top_frac", 0.16))
        self.bottom_frac = float(extra.get("leaderboard_bottom_frac", 0.85))
        width = int(extra.get("leaderboard_width_px", 96))
        left_px = int(extra.get("leaderboard_left_px", 0))

        LayerShell.init_for_window(self)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        monitor = None
        wanted = extra.get("overlay_monitor")
        if wanted:
            for m in Gdk.Display.get_default().get_monitors():
                if m.get_connector() == wanted:
                    monitor = m
                    LayerShell.set_monitor(self, m)
                    break
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, left_px)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        self._height = monitor.get_geometry().height if monitor else 1440
        self.set_default_size(width, -1)

        # fully transparent content — the strip is invisible unless
        # hover_debug is set, which tints the band and slot boundaries.
        self.debug = bool(extra.get("hover_debug", False))
        area = Gtk.DrawingArea()
        if self.debug:
            area.set_draw_func(self._draw_debug)
        self.set_child(area)
        css = Gtk.CssProvider()
        css.load_from_data(b"window { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)
        self.connect("realize", self._set_input_region)

    def _band(self) -> tuple[int, int]:
        top = int(self._height * self.top_frac)
        bottom = int(self._height * self.bottom_frac)
        return top, bottom

    def _set_input_region(self, *_):
        import cairo

        surface = self.get_surface()
        if surface is None:
            return
        if self.debug:
            # calibration: capture the whole column so pointer y is visible
            # anywhere along the left edge
            region = cairo.Region(
                cairo.RectangleInt(0, 0, self.get_width() or 96, self._height)
            )
        else:
            top, bottom = self._band()
            region = cairo.Region(
                cairo.RectangleInt(0, top, self.get_width() or 96, bottom - top)
            )
        surface.set_input_region(region)

    def _slot_at(self, y: float) -> int | None:
        top, bottom = self._band()
        if not (top <= y < bottom):
            return None
        idx = int((y - top) / (bottom - top) * self.slots)
        return min(idx, self.slots - 1)

    def _draw_debug(self, _area, cr, width, _height):
        top, bottom = self._band()
        cr.set_source_rgba(1, 0.2, 0.2, 0.25)
        cr.rectangle(0, top, width, bottom - top)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.6)
        for i in range(1, self.slots):
            y = top + (bottom - top) * i / self.slots
            cr.rectangle(0, y, width, 1)
            cr.fill()

    _cal_bucket = None

    def _on_motion(self, _ctrl, _x, y):
        if self.debug:
            bucket = int(y // 25)
            if bucket != self._cal_bucket:
                self._cal_bucket = bucket
                print(f"cal-y: {y:.0f}")
        slot = self._slot_at(y)
        if slot != self._current:
            self._current = slot
            if self.debug:
                print(f"hover: slot {slot} (y={y:.0f})")
            self.on_slot(slot)

    def _on_leave(self, _ctrl):
        if self._current is not None:
            self._current = None
            self.on_slot(None)
