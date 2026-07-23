"""Transparent layer-shell overlay with draggable HUD panels (KWin Wayland).

The window fills the monitor and is normally click-through (empty input
region). Each HUD group is an independently positioned panel on a Gtk.Fixed.

Layout mode (config `overlay_edit = true`): the panels grow a drag handle and
a dashed border, the window accepts pointer input over the panels only (empty
space still passes clicks to the game), and dragging a panel saves its new
position to config. An on-screen "Lock layout" button flips edit mode back off
live — no restart needed.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

from bgtracker.config import Config, update_config_values  # noqa: E402
from bgtracker.data import cards  # noqa: E402

from .widgets import BoardPanel  # noqa: E402

CSS_TEMPLATE = """
window {{ background: transparent; }}
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
.grip {{ color: #7fd0ff; font-size: {dim_px}px; font-weight: bold; }}
.editing {{ border: 2px dashed rgba(127, 208, 255, 0.9); }}
.lockbtn {{
    background-color: rgba(30, 120, 200, 0.96);
    color: #ffffff; border-radius: 8px;
    padding: 8px 16px; font-weight: bold;
}}
"""


class OverlayWindow(Gtk.Window):
    def __init__(self, application: Gtk.Application, cfg: Config | None = None):
        super().__init__(application=application)
        cfg = cfg or Config()
        self.scale = scale = max(0.5, min(3.0, cfg.overlay_scale))
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

        self.canvas = Gtk.Fixed()
        self.set_child(self.canvas)

        self._panels: dict[str, Gtk.Widget] = {}
        self._grips: dict[str, Gtk.Label] = {}
        self._pos: dict[str, list[int]] = {}
        self._has_content: dict[str, bool] = {}
        self._drag_base = (0, 0)
        self.lock_btn: Gtk.Button | None = None

        # -- HUD panel: status, odds, damage, and the live/combat board -----
        self.status = Gtk.Label(label="waiting for game…", xalign=0)
        self.status.add_css_class("dim")
        self.odds = Gtk.Label(label="", xalign=0)
        self.odds.add_css_class("odds")
        self.damage = Gtk.Label(label="", xalign=0)
        self.damage.add_css_class("dim")
        self.board = BoardPanel()
        self._make_panel("hud", "HUD", [self.status, self.odds, self.damage, self.board])

        # -- buffs panel: accumulating tavern buffs, its own movable view ---
        self.buffs = Gtk.Label(label="", xalign=0, wrap=True)
        self.buffs.add_css_class("stats")
        self._make_panel("buffs", "Buffs", [self.buffs])

        # -- next-opponent panel: last-seen board as card tiles -------------
        self.next_board = BoardPanel()
        self._make_panel("next", "Next opponent", [self.next_board])

        # -- hover panel: last-seen board of a leaderboard portrait ---------
        self.hover_board = BoardPanel()
        self._make_panel("hover", "Hover board", [self.hover_board])

        defaults = {
            "hud": (self._mon_w - int(400 * scale), int(40)),
            "next": (self._mon_w - int(400 * scale), int(360 * scale)),
            "hover": (int(self._mon_w * 0.13), int(self._mon_h * 0.30)),
            "buffs": (int(self._mon_w * 0.34), int(40)),
        }
        for name, (dx, dy) in defaults.items():
            x = int(cfg.extra.get(f"pos_{name}_x", dx))
            y = int(cfg.extra.get(f"pos_{name}_y", dy))
            self._pos[name] = [x, y]
            self.canvas.put(self._panels[name], x, y)

        # hud always shows; the rest only when they have something (or edit)
        self._set_content("hud", True)
        self._set_content("next", False)
        self._set_content("hover", False)
        self._set_content("buffs", False)

        if self.edit:
            self.lock_btn = Gtk.Button(label="✔ Lock layout")
            self.lock_btn.add_css_class("lockbtn")
            self.lock_btn.connect("clicked", self._on_lock)
            self._lock_pos = (self._mon_w // 2 - int(90 * scale), 24)
            self.canvas.put(self.lock_btn, *self._lock_pos)

        provider = Gtk.CssProvider()
        provider.load_from_data(
            CSS_TEMPLATE.format(
                pad=int(12 * scale), pad2=int(16 * scale),
                odds_px=int(22 * scale), dim_px=int(12 * scale),
                line_px=int(13 * scale),
            ).encode()
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.connect("realize", self._apply_input_region)
        self.connect("map", lambda *_: GLib.idle_add(self._apply_input_region))

    # -- panel construction / dragging ----------------------------------
    def _make_panel(self, name: str, title: str, content: list[Gtk.Widget]) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("hud")
        box.set_size_request(int(220 * self.scale), -1)
        grip = Gtk.Label(label=f"⠿ {title}", xalign=0)
        grip.add_css_class("grip")
        grip.set_visible(self.edit)
        box.append(grip)
        for w in content:
            box.append(w)
        if self.edit:
            box.add_css_class("editing")
        self._panels[name] = box
        self._grips[name] = grip
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin, name)
        drag.connect("drag-update", self._drag_update, name)
        drag.connect("drag-end", self._drag_end, name)
        box.add_controller(drag)

    def _drag_begin(self, _gesture, _sx, _sy, name):
        self._drag_base = tuple(self._pos[name])

    def _drag_update(self, _gesture, ox, oy, name):
        bx, by = self._drag_base
        nx = max(0, min(self._mon_w - 40, int(bx + ox)))
        ny = max(0, min(self._mon_h - 20, int(by + oy)))
        self._pos[name] = [nx, ny]
        self.canvas.move(self._panels[name], nx, ny)
        self._apply_input_region()

    def _drag_end(self, _gesture, _ox, _oy, name):
        x, y = self._pos[name]
        update_config_values({f"pos_{name}_x": x, f"pos_{name}_y": y})

    def _on_lock(self, _btn):
        self.edit = False
        update_config_values({"overlay_edit": False})
        for name, panel in self._panels.items():
            self._grips[name].set_visible(False)
            panel.remove_css_class("editing")
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
        region = cairo.Region()
        items = [(self._pos[n][0], self._pos[n][1], w)
                 for n, w in self._panels.items() if w.get_visible()]
        if self.lock_btn is not None:
            items.append((self._lock_pos[0], self._lock_pos[1], self.lock_btn))
        pad = 8
        for x, y, w in items:
            _, nat = w.get_preferred_size()
            region.union(cairo.RectangleInt(
                int(x - pad), int(y - pad),
                int(nat.width + 2 * pad), int(nat.height + 2 * pad),
            ))
        surface.set_input_region(region)
        return False

    def _set_content(self, name: str, has_content: bool) -> None:
        self._has_content[name] = has_content
        self._panels[name].set_visible(self.edit or has_content)
        if self.edit:
            self._apply_input_region()

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

    def set_buffs(self, entries, spells=()) -> None:
        lines = [
            f'<span foreground="#e0b0ff">{label}</span> +{atk}/+{hp}'
            for label, atk, hp in entries
        ]
        for cid in spells:
            name = GLib.markup_escape_text(cards.name(cid))
            lines.append(f'<span foreground="#7fd0ff">⚡ {name}</span>')
        if not lines:
            self.buffs.set_label("")
            self._set_content("buffs", False)
            return
        self.buffs.set_markup("\n".join(lines))
        self._set_content("buffs", True)

    def set_board(self, title: str, board) -> None:
        self.board.show_board(title, board)

    def clear_board(self) -> None:
        self.board.clear()

    def set_next_board(self, title: str, board) -> None:
        self.next_board.show_board(title, board)
        self._set_content("next", True)

    def clear_next_board(self) -> None:
        self.next_board.clear()
        self._set_content("next", False)

    def set_hover_board(self, title: str, board) -> None:
        self.hover_board.show_board(title, board)
        self._set_content("hover", True)

    def clear_hover_board(self) -> None:
        self.hover_board.clear()
        self._set_content("hover", False)
