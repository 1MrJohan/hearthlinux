"""Transparent layer-shell overlay with draggable HUD panels (KWin Wayland).

The window fills the monitor and is normally click-through (empty input
region). Each HUD group is an independently positioned panel on a Gtk.Fixed.

Layout mode (config `overlay_edit = true`): the panels grow a drag handle and
a dashed border, the window accepts pointer input over the panels only (empty
space still passes clicks to the game), and dragging a panel saves its new
position to config. An on-screen "Lock layout" button flips edit mode back off
live — no restart needed.

Styling lives in `theme.py`; this module is layout and plumbing only.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

from bgtracker.config import Config, update_config_values  # noqa: E402
from bgtracker.data import cards  # noqa: E402

from . import theme  # noqa: E402
from .hud import HudPanel  # noqa: E402
from .rail import LeaderboardRail  # noqa: E402
from .widgets import BoardPanel  # noqa: E402

log = logging.getLogger(__name__)

# Minimum grabbable body for a panel in layout mode, so an empty one is still
# big enough to aim at.
GRAB_MIN_W, GRAB_MIN_H = 160, 60


class OverlayWindow(Gtk.Window):
    def __init__(self, application: Gtk.Application, cfg: Config | None = None):
        super().__init__(application=application)
        cfg = cfg or Config()
        self.edit = bool(cfg.extra.get("overlay_edit", False))

        LayerShell.init_for_window(self)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        monitor = None
        wanted = cfg.extra.get("overlay_monitor")
        for m in Gdk.Display.get_default().get_monitors():
            if wanted and m.get_connector() == wanted:
                monitor = m
                LayerShell.set_monitor(self, m)
                break
        # Fill the monitor; panels are placed absolutely on the Fixed canvas.
        for edge in (
            LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
            LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT,
        ):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        geo = monitor.get_geometry() if monitor else None
        self._mon_w = geo.width if geo else 2560
        self._mon_h = geo.height if geo else 1440

        # Scale needs the monitor, so it is resolved here rather than up front:
        # an unset overlay_scale means "match the mock's proportions on this
        # display" instead of silently rendering 1180px-stage sizes 1:1.
        self.scale = scale = (
            theme.auto_scale(self._mon_h)
            if cfg.overlay_scale is None
            else theme.clamp_scale(cfg.overlay_scale)
        )
        log.info(
            "overlay scale %.2f on %s (%dx%d)%s", scale,
            monitor.get_connector() if monitor else "default monitor",
            self._mon_w, self._mon_h,
            "" if cfg.overlay_scale is None else " — from overlay_scale",
        )

        self.canvas = Gtk.Fixed()
        self.set_child(self.canvas)

        self._panels: dict[str, Gtk.Widget] = {}
        self._frames: dict[str, Gtk.Widget] = {}
        self._grips: dict[str, Gtk.Label] = {}
        self._pos: dict[str, list[int]] = {}
        self._has_content: dict[str, bool] = {}
        self._drag_base = (0, 0)
        self._dragging: str | None = None
        self._region_rects: list[tuple[int, int, int, int]] = []
        self.lock_btn: Gtk.Button | None = None

        # -- HUD panel: phase, turn medallion, odds, damage -----------------
        self.hud = HudPanel(scale)
        self._make_panel("hud", "HUD", [self.hud], width=theme.HUD_W)

        # -- enemy board: the opponent you are about to fight ---------------
        self.board = BoardPanel(scale=scale)
        self._make_panel("board", "Enemy board", [self.board])

        # -- buffs panel: accumulating tavern buffs, its own movable view ---
        self.buffs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(5, scale))
        buffs_title = Gtk.Label(label="Tavern Buffs", xalign=0)
        buffs_title.add_css_class("title")
        self._make_panel("buffs", "Buffs", [buffs_title, self.buffs], width=theme.BUFFS_W)
        self._buff_rows: list[Gtk.Widget] = []

        # -- next-opponent panel: last-seen board as minion tiles -----------
        self.next_board = BoardPanel(scale=scale)
        self._make_panel("next", "Next opponent", [self.next_board])

        # -- scout popout: last-seen board of a leaderboard portrait --------
        self.hover_board = BoardPanel(show_orb=True, scale=scale)
        notch = Gtk.Label(label="◀")
        notch.add_css_class("notch")
        notch.set_valign(Gtk.Align.START)
        notch.set_margin_top(theme.px(22, scale))
        self._make_panel("hover", "Scout", [self.hover_board], leading=notch)

        # -- leaderboard rail: standings with hero orbs ---------------------
        self.rail = LeaderboardRail(scale)
        self._make_panel("rail", "Leaderboard", [self.rail], width=theme.RAIL_W,
                         frame_class="rail")

        defaults = {
            "hud": (self._mon_w - int(400 * scale), int(40)),
            "board": (self._mon_w // 2 - int(320 * scale), int(12 * scale)),
            "next": (self._mon_w - int(400 * scale), int(360 * scale)),
            "hover": (int(self._mon_w * 0.13), int(self._mon_h * 0.30)),
            "buffs": (int(self._mon_w * 0.34), int(40)),
            "rail": (int(12 * scale), int(self._mon_h * 0.08)),
        }
        for name, (dx, dy) in defaults.items():
            x = int(cfg.extra.get(f"pos_{name}_x", dx))
            y = int(cfg.extra.get(f"pos_{name}_y", dy))
            self._pos[name] = [x, y]
            self.canvas.put(self._panels[name], x, y)

        # hud always shows; the rest only when they have something (or edit)
        self._set_content("hud", True)
        for name in ("board", "next", "hover", "buffs", "rail"):
            self._set_content(name, False)

        if self.edit:
            self.lock_btn = Gtk.Button(label="✔ Lock layout")
            self.lock_btn.add_css_class("lockbtn")
            self.lock_btn.connect("clicked", self._on_lock)
            # Bottom-centre, as in the mock. Emphatically not the top: that sits
            # over Hearthstone's own UI, where a stray click silently drops you
            # out of layout mode with no way back but editing the config.
            self._lock_pos = (self._mon_w // 2 - int(90 * scale),
                              self._mon_h - int(100 * scale))
            self.canvas.put(self.lock_btn, *self._lock_pos)

        theme.install(self.get_display(), scale)
        self.connect("realize", self._apply_input_region)
        self.connect("map", lambda *_: GLib.idle_add(self._on_mapped))

    def _on_mapped(self) -> bool:
        for name in self._panels:
            self._clamp_panel(name)
        if self.lock_btn is not None:
            # Centre it exactly now that its size is known.
            _, nat = self.lock_btn.get_preferred_size()
            self._lock_pos = (
                max(0, self._mon_w // 2 - nat.width // 2),
                max(0, self._mon_h - nat.height - int(14 * self.scale)),
            )
            self.canvas.move(self.lock_btn, *self._lock_pos)
        return self._apply_input_region()

    # -- panel construction / dragging ----------------------------------
    def _make_panel(
        self,
        name: str,
        title: str,
        content: list[Gtk.Widget],
        width: int | None = None,
        leading: Gtk.Widget | None = None,
        frame_class: str | None = None,
    ) -> None:
        """Build a draggable panel.

        `width` fixes the frame width in design px (scaled). `leading` is a
        decoration placed *outside* the oak frame — the scout popout's notch.
        `frame_class` adds a variant class to the frame (the rail's sub-frame).
        """
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=theme.px(4, self.scale))
        frame.add_css_class("hud")
        if frame_class:
            frame.add_css_class(frame_class)
        frame.set_size_request(
            theme.px(width, self.scale) if width else int(220 * self.scale), -1
        )
        grip = Gtk.Label(label=f"⠿ {title}", xalign=0)
        grip.add_css_class("grip")
        grip.set_visible(self.edit)
        frame.append(grip)
        for w in content:
            frame.append(w)
        if self.edit:
            frame.add_css_class("editing")

        if leading is not None:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            box.append(leading)
            box.append(frame)
        else:
            box = frame

        self._panels[name] = box
        self._frames[name] = frame
        self._grips[name] = grip
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin, name)
        drag.connect("drag-update", self._drag_update, name)
        drag.connect("drag-end", self._drag_end, name)
        box.add_controller(drag)

    def _drag_begin(self, _gesture, _sx, _sy, name):
        self._drag_base = tuple(self._pos[name])
        self._dragging = name

    def _drag_update(self, _gesture, ox, oy, name):
        bx, by = self._drag_base
        nx = max(0, min(self._mon_w - 40, int(bx + ox)))
        ny = max(0, min(self._mon_h - 20, int(by + oy)))
        self._pos[name] = [nx, ny]
        self.canvas.move(self._panels[name], nx, ny)
        self._apply_input_region()

    def _drag_end(self, _gesture, _ox, _oy, name):
        self._dragging = None
        x, y = self._pos[name]
        update_config_values({f"pos_{name}_x": x, f"pos_{name}_y": y})

    def _on_lock(self, _btn):
        self.edit = False
        update_config_values({"overlay_edit": False})
        for name, panel in self._panels.items():
            self._grips[name].set_visible(False)
            self._frames[name].remove_css_class("editing")
            panel.set_visible(self._has_content.get(name, False))
        if self.lock_btn is not None:
            self.canvas.remove(self.lock_btn)
            self.lock_btn = None
        self._apply_input_region()

    # -- input region: click-through when locked, panel-bounded in edit --
    def _apply_input_region(self, *_):
        surface = self.get_surface()
        if surface is None:
            return False
        import cairo

        if not self.edit:
            surface.set_input_region(cairo.Region())  # fully click-through
            return False
        items = [(self._pos[n][0], self._pos[n][1], w)
                 for n, w in self._panels.items() if w.get_visible()]
        if self.lock_btn is not None:
            items.append((self._lock_pos[0], self._lock_pos[1], self.lock_btn))
        pad = 8
        rects = []
        for x, y, w in items:
            _, nat = w.get_preferred_size()
            # An empty panel in layout mode is barely a grip's worth of pixels;
            # give it a grabbable body so it can still be dragged into place.
            width = max(nat.width, GRAB_MIN_W)
            height = max(nat.height, GRAB_MIN_H)
            rects.append((int(x - pad), int(y - pad),
                          int(width + 2 * pad), int(height + 2 * pad)))
        # Panel sizes change with every board update; re-uploading an identical
        # region hundreds of times a second is pure churn.
        if rects == self._region_rects:
            return False
        self._region_rects = rects
        log.debug("input region: %d rect(s) %s", len(rects), rects)
        region = cairo.Region()
        for rect in rects:
            region.union(cairo.RectangleInt(*rect))
        surface.set_input_region(region)
        return False

    def _set_content(self, name: str, has_content: bool) -> None:
        self._has_content[name] = has_content
        self._panels[name].set_visible(self.edit or has_content)
        self._clamp_panel(name)
        if self.edit:
            self._apply_input_region()

    def _clamp_panel(self, name: str) -> None:
        """Nudge a panel back on-screen if its content has outgrown its position.

        A board's width depends on how many minions it holds, and every size
        scales with the monitor, so a position saved at one scale can put a
        full seven-minion board off the right edge. Display-only — the config
        keeps whatever the user dragged, so this never silently rewrites their
        layout.
        """
        panel = self._panels[name]
        if not panel.get_visible() or self._dragging == name:
            return  # never yank a panel out from under the pointer mid-drag
        _, nat = panel.get_preferred_size()
        if nat.width <= 0 or nat.height <= 0:
            return
        x, y = self._pos[name]
        nx = max(0, min(x, self._mon_w - nat.width))
        ny = max(0, min(y, self._mon_h - nat.height))
        if (nx, ny) != (x, y):
            self._pos[name] = [nx, ny]
            self.canvas.move(panel, nx, ny)

    def _panel_rect(self, name: str) -> tuple[int, int, int, int] | None:
        panel = self._panels[name]
        if not panel.get_visible():
            return None
        _, nat = panel.get_preferred_size()
        if nat.width <= 0 or nat.height <= 0:
            return None
        x, y = self._pos[name]
        return (x, y, nat.width, nat.height)

    def rail_rect(self) -> tuple[int, int, int, int] | None:
        """Monitor-space rect of the leaderboard rail, for the hover poll."""
        return self._panel_rect("rail")

    def hud_rect(self) -> tuple[int, int, int, int] | None:
        """Monitor-space rect of the HUD, for the hover poll."""
        return self._panel_rect("hud")

    def set_hud_hovered(self, hovered: bool) -> None:
        self.hud.set_hovered(hovered)

    def set_forecast_live(self, live: bool) -> None:
        self.hud.set_forecast_live(live)

    # -- update API (call from the GLib/asyncio loop) -------------------
    def set_phase(self, title: str, meta: str = "") -> None:
        self.hud.set_phase(title, meta)

    def set_status(self, text: str) -> None:
        self.hud.set_status(text)

    def set_turn(self, turn: int | None) -> None:
        self.hud.set_turn(turn)

    def set_combat(self, combat: bool) -> None:
        self.hud.set_combat(combat)

    def set_odds(self, win: float | None, tie: float | None, loss: float | None) -> None:
        self.hud.set_odds(win, tie, loss)
        if win is None:
            self.hud.set_damage(None, None)

    def set_damage(self, dealt: float | None, taken: float | None) -> None:
        self.hud.set_damage(dealt, taken)

    def set_buffs(self, entries, spells=()) -> None:
        for row in self._buff_rows:
            self.buffs.remove(row)
        self._buff_rows.clear()
        for label, atk, hp in entries:
            self._buff_rows.append(self._buff_chip(label, f"+{atk}/+{hp}"))
        for card_id in spells:
            self._buff_rows.append(self._spell_chip(cards.name(card_id)))
        for row in self._buff_rows:
            self.buffs.append(row)
        self._set_content("buffs", bool(self._buff_rows))

    def _buff_chip(self, label: str, value: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=theme.px(7, self.scale))
        dot = Gtk.Box()
        dot.add_css_class("buff-dot")
        dot.add_css_class(f"buff-{label.lower().replace(' ', '')}")
        dot.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=label, xalign=0)
        name.add_css_class("buff-label")
        name.set_hexpand(True)
        amount = Gtk.Label(label=value, xalign=1)
        amount.add_css_class("buff-val")
        row.append(dot)
        row.append(name)
        row.append(amount)
        return row

    def _spell_chip(self, name: str) -> Gtk.Widget:
        label = Gtk.Label(label=f"✦ {name}", xalign=0)
        label.add_css_class("spell")
        label.set_wrap(True)
        return label

    def set_board(self, title: str, subtitle: str = "", board=None) -> None:
        self.board.show_board(title, subtitle, board)
        self._set_content("board", True)

    def clear_board(self) -> None:
        self.board.clear()
        self._set_content("board", False)

    def set_next_board(self, title: str, subtitle: str = "", board=None) -> None:
        self.next_board.show_board(title, subtitle, board)
        self._set_content("next", True)

    def clear_next_board(self) -> None:
        self.next_board.clear()
        self._set_content("next", False)

    def set_hover_board(self, title: str, subtitle: str = "", board=None,
                        hero_card_id: str | None = None, dead: bool = False) -> None:
        self.hover_board.show_board(title, subtitle, board, hero_card_id, dead)
        self._set_content("hover", True)

    def clear_hover_board(self) -> None:
        self.hover_board.clear()
        self._set_content("hover", False)

    def set_standings(self, standings) -> None:
        self.rail.set_standings(standings)
        self._set_content("rail", bool(standings))

    def set_hot_place(self, place: int | None) -> None:
        self.rail.set_hot(place)
