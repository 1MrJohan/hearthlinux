"""Hover boxes over the BG leaderboard portraits.

A layer-shell column overlays the in-game leaderboard. In LIVE mode it does NOT
capture the pointer — the input region is empty (click-through), so Blizzard's
own board-preview-on-hover still fires — and instead the X11 cursor position
(the game runs under XWayland) is polled to detect which portrait box the
pointer is over, driving the overlay's Hover panel. Native preview + overlay
board show together.

The box column can be tilted: the leaderboard leans slightly, so each box's x
is interpolated from the top box (`leaderboard_left_px`) to the bottom box
(top + `leaderboard_skew_px`).

Two modes, driven by config:
  * overlay_edit=true  — LAYOUT mode: boxes are drawn and adjustable by drag —
      body = move, blue bottom edge = height, green right edge = width,
      yellow top edge = tilt/angle. Geometry saves on release.
  * overlay_edit=false — LIVE mode: hovering a box shows that player's board
      alongside the game's native preview. Boxes are drawn as a faint guide
      when hover_debug=true, otherwise invisible.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

from bgtracker.config import Config, update_config_values  # noqa: E402

log = logging.getLogger(__name__)


class HoverStrips(Gtk.Window):
    """on_slot(index | None) fires as the pointer enters/leaves portrait boxes."""

    def __init__(
        self,
        application: Gtk.Application,
        cfg: Config,
        on_slot: Callable[[int | None], None],
        slots: int = 8,
        rail_rect: Callable[[], tuple[int, int, int, int] | None] | None = None,
    ):
        super().__init__(application=application)
        self.on_slot = on_slot
        self.slots = slots
        # Our own leaderboard rail is hovered through the same pointer poll, so
        # it needs no input region and the overlay stays click-through.
        self.rail_rect = rail_rect
        self._current: int | None = None

        extra = cfg.extra
        self.top_frac = float(extra.get("leaderboard_top_frac", 0.16))
        self.bottom_frac = float(extra.get("leaderboard_bottom_frac", 0.85))
        self.strip_width = int(extra.get("leaderboard_width_px", 96))
        self._left_px = int(extra.get("leaderboard_left_px", 0))
        self.skew = int(extra.get("leaderboard_skew_px", 0))
        self._drag_base: tuple = (0, 0, 0, 0, 0)
        self._drag_mode = "move"

        self.edit = bool(extra.get("overlay_edit", False))
        self.draw = self.edit or bool(extra.get("hover_debug", False))

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
        # Boxes are drawn at absolute monitor-x, so the window sits flush to the
        # left edge (margin 0) and is only as wide as it needs to cover them.
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, 0)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        geo = monitor.get_geometry() if monitor else None
        self._height = geo.height if geo else 1440
        self._mon_w = geo.width if geo else 2560
        span = self._left_px + self.strip_width + abs(self.skew)
        self.set_default_size(min(self._mon_w, max(span + 220, 600)), -1)

        area = Gtk.DrawingArea()
        if self.draw:
            area.set_draw_func(self._draw_debug)
        self.set_child(area)
        css = Gtk.CssProvider()
        css.load_from_data(b"window { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.connect("realize", self._set_input_region)

        if self.edit:
            drag = Gtk.GestureDrag()
            drag.set_button(1)
            drag.connect("drag-begin", self._drag_begin)
            drag.connect("drag-update", self._drag_update)
            drag.connect("drag-end", self._drag_end)
            self.add_controller(drag)
            print(
                "HOVER BOX EDIT: body = move, yellow top edge = angle-from-top, "
                "bottom-left = angle-from-bottom, bottom-right (blue) = height, "
                "green right edge = width. Saves on release.",
                flush=True,
            )
        else:
            # LIVE: capture nothing (game keeps its native preview); poll the
            # X11 cursor to detect which box we're over.
            self._setup_pointer_poll(wanted)

    # -- geometry -------------------------------------------------------
    def _band(self) -> tuple[int, int]:
        return int(self._height * self.top_frac), int(self._height * self.bottom_frac)

    def _box_x(self, i: int) -> int:
        return self._left_px + round(self.skew * i / max(self.slots - 1, 1))

    def _slot_at_xy(self, lx: float, ly: float) -> int | None:
        top, bottom = self._band()
        if not (top <= ly < bottom):
            return None
        s = min(int((ly - top) / (bottom - top) * self.slots), self.slots - 1)
        x = self._box_x(s)
        return s if x <= lx < x + self.strip_width else None

    def _rail_slot_at_xy(self, lx: float, ly: float) -> int | None:
        """Which row of our own leaderboard rail the pointer is over."""
        if self.rail_rect is None:
            return None
        rect = self.rail_rect()
        if rect is None:
            return None
        x, y, w, h = rect
        if h <= 0 or not (x <= lx < x + w and y <= ly < y + h):
            return None
        return min(int((ly - y) / h * self.slots), self.slots - 1)

    def _set_input_region(self, *_):
        import cairo

        surface = self.get_surface()
        if surface is None:
            return
        if not self.edit:
            surface.set_input_region(cairo.Region())  # live: click-through
            return
        top, bottom = self._band()
        slot_h = (bottom - top) / self.slots
        region = cairo.Region()
        for i in range(self.slots):
            x = self._box_x(i)
            y = int(top + i * slot_h)
            region.union(
                cairo.RectangleInt(x - 6, y - 6, self.strip_width + 24, int(slot_h) + 12)
            )
        surface.set_input_region(region)

    def _draw_debug(self, _area, cr, _width, _height):
        top, bottom = self._band()
        slot_h = (bottom - top) / self.slots
        w = self.strip_width
        for i in range(self.slots):
            x = self._box_x(i)
            y = top + i * slot_h
            cr.set_source_rgba(1, 0.25, 0.25, 0.20)
            cr.rectangle(x + 1, y + 1, w - 2, slot_h - 2)
            cr.fill()
            cr.set_source_rgba(1, 0.35, 0.35, 0.85)
            cr.set_line_width(2)
            cr.rectangle(x + 1, y + 1, w - 2, slot_h - 2)
            cr.stroke()
        if self.edit:
            x0, xN = self._box_x(0), self._box_x(self.slots - 1)
            half = w / 2
            cr.set_source_rgba(1.0, 0.85, 0.3, 0.9)   # yellow top = angle-from-top
            cr.rectangle(x0, top - 6, w, 12)
            cr.fill()
            # bottom split: yellow left = angle-from-bottom, blue right = height
            cr.set_source_rgba(1.0, 0.85, 0.3, 0.9)
            cr.rectangle(xN, bottom - 6, half, 12)
            cr.fill()
            cr.set_source_rgba(0.5, 0.82, 1.0, 0.9)
            cr.rectangle(xN + half, bottom - 6, w - half, 12)
            cr.fill()
            cr.set_source_rgba(0.5, 1.0, 0.7, 0.9)    # green right = width
            cr.rectangle(x0 + w - 6, top, 14, slot_h)
            cr.fill()

    # -- live pointer polling (X11 / XWayland) --------------------------
    def _setup_pointer_poll(self, connector):
        try:
            from Xlib import display as xdisplay
            from Xlib.ext import randr
        except Exception as exc:
            log.warning("python-xlib missing; native+overlay hover unavailable (%s)", exc)
            return
        try:
            self._xdisp = xdisplay.Display()
            self._xroot = self._xdisp.screen().root
            self._mon_x, self._mon_y = 0, 0
            if connector:
                res = randr.get_screen_resources(self._xroot)
                for out in res.outputs:
                    oi = randr.get_output_info(self._xroot, out, res.config_timestamp)
                    name = oi.name if isinstance(oi.name, str) else bytes(oi.name).decode(errors="replace")
                    if oi.crtc and name == connector:
                        ci = randr.get_crtc_info(self._xroot, oi.crtc, res.config_timestamp)
                        self._mon_x, self._mon_y = ci.x, ci.y
                        break
        except Exception as exc:
            log.warning("pointer poll setup failed (%s)", exc)
            return
        log.info("live hover via X11 pointer poll; monitor origin (%d,%d)", self._mon_x, self._mon_y)
        GLib.timeout_add(80, self._poll_pointer)

    def _poll_pointer(self):
        try:
            p = self._xroot.query_pointer()
        except Exception:
            return True  # transient; keep polling
        lx, ly = p.root_x - self._mon_x, p.root_y - self._mon_y
        slot = self._slot_at_xy(lx, ly)
        if slot is None:
            slot = self._rail_slot_at_xy(lx, ly)
        if slot != self._current:
            self._current = slot
            self.on_slot(slot)
        return True

    # -- drag-to-align the boxes (overlay_edit) ------------------------
    def _drag_begin(self, _gesture, sx, sy):
        top, bottom = self._band()
        self._drag_base = (self._left_px, self.skew, top, bottom, self.strip_width)
        if sy <= top + 22:
            self._drag_mode = "skew_top"     # angle from the top (pivot bottom)
        elif sy >= bottom - 22:
            # bottom edge is split: left half tilts, right half extends
            xN = self._box_x(self.slots - 1)
            self._drag_mode = "skew_bottom" if sx < xN + self.strip_width / 2 else "resize"
        else:
            s = min(max(int((sy - top) / max(bottom - top, 1) * self.slots), 0), self.slots - 1)
            self._drag_mode = "width" if abs(sx - (self._box_x(s) + self.strip_width)) <= 16 else "move"

    def _drag_update(self, _gesture, ox, oy):
        base_left, base_skew, base_top, base_bottom, base_w = self._drag_base
        h = self._height
        m = self._drag_mode
        if m == "width":
            self.strip_width = max(30, min(self.get_width() or base_w, int(base_w + ox)))
        elif m == "skew_top":
            # move the top box in x (bottom stays put) and set the top height
            self._left_px = max(0, int(base_left + ox))
            self.skew = int(base_skew - ox)
            self.top_frac = max(0.0, (base_top + oy) / h)
        elif m == "skew_bottom":
            # move the bottom box in x (top stays put)
            self.skew = int(base_skew + ox)
        elif m == "resize":
            self.bottom_frac = min(h, max(base_top + 40, base_bottom + oy)) / h
        else:  # move whole band
            self._left_px = max(0, int(base_left + ox))
            self.top_frac = max(0.0, (base_top + oy) / h)
            self.bottom_frac = min(1.0, (base_bottom + oy) / h)
        self._set_input_region()
        child = self.get_child()
        if child is not None:
            child.queue_draw()

    def _drag_end(self, _gesture, _ox, _oy):
        self._save_geometry()

    def _save_geometry(self):
        update_config_values(
            {
                "leaderboard_top_frac": round(self.top_frac, 4),
                "leaderboard_bottom_frac": round(self.bottom_frac, 4),
                "leaderboard_left_px": int(self._left_px),
                "leaderboard_width_px": self.strip_width,
                "leaderboard_skew_px": int(self.skew),
            }
        )
        print(
            f"hover boxes saved: top={self.top_frac:.4f} bottom={self.bottom_frac:.4f} "
            f"left={self._left_px} width={self.strip_width} skew={self.skew}",
            flush=True,
        )
