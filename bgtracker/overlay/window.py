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

from . import theme  # noqa: E402
from .hud import HudPanel  # noqa: E402
from .rail import LeaderboardRail  # noqa: E402
from .widgets import BoardPanel  # noqa: E402

log = logging.getLogger(__name__)

# Minimum grabbable body for a panel in layout mode, so an empty one is still
# big enough to aim at.
GRAB_MIN_W, GRAB_MIN_H = 160, 60


class OverlayWindow(Gtk.Window):
    def __init__(
        self,
        application: Gtk.Application,
        cfg: Config | None = None,
        on_settings=None,
        on_edit=None,
        on_mmr=None,
    ):
        super().__init__(application=application)
        self.cfg = cfg = cfg or Config()
        self.edit = bool(cfg.overlay_edit)
        self._on_settings = on_settings
        self._on_edit = on_edit
        self._on_mmr = on_mmr
        # The transparent-window rule is scoped to this class so it cannot leak
        # onto the settings window, which shares the display-wide provider.
        self.add_css_class("bg-overlay")

        LayerShell.init_for_window(self)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        monitor = None
        wanted = cfg.overlay_monitor
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

        # -- scout popout: last-seen board of a leaderboard portrait --------
        # The upcoming opponent and its shop forecast use this same popout;
        # there is no second, permanently visible copy of that board.
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
            "hover": (int(self._mon_w * 0.13), int(self._mon_h * 0.30)),
            "buffs": (int(self._mon_w * 0.34), int(40)),
            "rail": (int(12 * scale), int(self._mon_h * 0.08)),
        }
        for name, (dx, dy) in defaults.items():
            x = int(cfg.extra.get(f"pos_{name}_x", dx))
            y = int(cfg.extra.get(f"pos_{name}_y", dy))
            self._pos[name] = [x, y]
            self.canvas.put(self._panels[name], x, y)

        # -- gear: the only pixels on a locked overlay that take a click ----
        # Built before the first _set_content: that path re-places the gear,
        # so the attribute must exist by then.
        self.gear_btn: Gtk.Button | None = None
        self._gear_pos = (0, 0)
        if on_settings is not None:
            self.gear_btn = Gtk.Button(label="⚙")
            self.gear_btn.add_css_class("gearbtn")
            self.gear_btn.set_tooltip_text("Tracker settings")
            self.gear_btn.connect("clicked", lambda _b: self._on_settings())
            self.canvas.put(self.gear_btn, 0, 0)

        # -- MMR nudge: the second (and only other) clickable patch ---------
        # Hidden until a game ends. It is a hole in click-through exactly as
        # long as it is on screen, which is why nothing else raises it.
        self.mmr_btn: Gtk.Button | None = None
        self._mmr_pos = (0, 0)
        if on_mmr is not None:
            self.mmr_btn = Gtk.Button(label="⊕ Record MMR")
            self.mmr_btn.add_css_class("mmrbtn")
            self.mmr_btn.set_tooltip_text("Record your rating in match history")
            self.mmr_btn.set_visible(False)
            self.mmr_btn.connect("clicked", lambda _b: self._on_mmr())
            self.canvas.put(self.mmr_btn, 0, 0)

        # hud always shows; the rest only when they have something (or edit)
        self._set_content("hud", True)
        for name in ("board", "hover", "buffs", "rail"):
            self._set_content(name, False)

        if self.edit:
            self._add_lock_button()

        self.css_provider = theme.install(self.get_display(), scale)
        self.connect("realize", self._apply_input_region)
        self.connect("map", lambda *_: GLib.idle_add(self._on_mapped))

    def _add_lock_button(self) -> None:
        self.lock_btn = Gtk.Button(label="✔ Lock layout")
        self.lock_btn.add_css_class("lockbtn")
        self.lock_btn.connect("clicked", self._on_lock)
        # Bottom-centre, as in the mock. Emphatically not the top: that sits
        # over Hearthstone's own UI, where a stray click silently drops you
        # out of layout mode with no way back but editing the config.
        self._lock_pos = (self._mon_w // 2 - int(90 * self.scale),
                          self._mon_h - int(100 * self.scale))
        self.canvas.put(self.lock_btn, *self._lock_pos)

    def _on_mapped(self) -> bool:
        for name in self._panels:
            self._clamp_panel(name)
        self._centre_lock_button()
        self._place_chrome()
        return self._apply_input_region()

    def _centre_lock_button(self) -> bool:
        if self.lock_btn is None:
            return False
        # Centre it exactly now that its size is known.
        _, nat = self.lock_btn.get_preferred_size()
        self._lock_pos = (
            max(0, self._mon_w // 2 - nat.width // 2),
            max(0, self._mon_h - nat.height - int(14 * self.scale)),
        )
        self.canvas.move(self.lock_btn, *self._lock_pos)
        return False

    def _place_chrome(self) -> None:
        """Re-park the two free-floating buttons after anything moves the HUD."""
        self._place_gear()
        self._place_mmr()

    def _place_mmr(self) -> None:
        """Park the MMR nudge just below the HUD's bottom-right corner.

        The gear owns the space above; this takes below, for the same reason —
        empty space beside the panel the user positioned, never over its own
        content. Placed even while hidden, so it is already in the right spot
        the moment a game ends.
        """
        if self.mmr_btn is None:
            return
        _, nudge = self.mmr_btn.get_preferred_size()
        _, hud = self._panels["hud"].get_preferred_size()
        hx, hy = self._pos["hud"]
        x = max(0, min(self._mon_w - nudge.width, hx + hud.width - nudge.width))
        y = max(0, min(self._mon_h - nudge.height,
                       hy + hud.height + int(6 * self.scale)))
        if (x, y) != self._mmr_pos:
            self._mmr_pos = (x, y)
            self.canvas.move(self.mmr_btn, x, y)
            # Same trap as the gear: a move that skipped this would leave the
            # clickable patch behind at the old spot, invisible and swallowing
            # clicks meant for the game.
            self._apply_input_region()

    def _place_gear(self) -> None:
        """Park the gear just above the HUD's top-right corner.

        Above rather than on it: this is the one part of a locked overlay that
        swallows a click instead of passing it to Hearthstone, so it sits in
        empty space beside the panel the user already positioned, and never
        over the HUD's own content.
        """
        if self.gear_btn is None:
            return
        _, gear = self.gear_btn.get_preferred_size()
        _, hud = self._panels["hud"].get_preferred_size()
        hx, hy = self._pos["hud"]
        x = max(0, min(self._mon_w - gear.width, hx + hud.width - gear.width))
        y = max(0, hy - gear.height - int(4 * self.scale))
        if (x, y) != self._gear_pos:
            self._gear_pos = (x, y)
            self.canvas.move(self.gear_btn, x, y)
            # The gear is the whole input region while locked, so a move that
            # did not re-upload it would leave the clickable patch behind at
            # the old spot — invisible, and swallowing clicks meant for the game.
            self._apply_input_region()

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
        # Kept on the live Config too, not just on disk: "reset panel layout"
        # works out what to forget from `cfg.extra`, and would miss anything
        # dragged since the process started.
        self.cfg.extra[f"pos_{name}_x"] = x
        self.cfg.extra[f"pos_{name}_y"] = y
        update_config_values({f"pos_{name}_x": x, f"pos_{name}_y": y})
        self._place_chrome()

    def set_edit(self, edit: bool) -> None:
        """Enter or leave layout mode without a restart."""
        if edit == self.edit:
            return
        self.edit = edit
        for name, panel in self._panels.items():
            self._grips[name].set_visible(edit)
            if edit:
                self._frames[name].add_css_class("editing")
            else:
                self._frames[name].remove_css_class("editing")
            panel.set_visible(edit or self._has_content.get(name, False))
        if edit and self.lock_btn is None:
            self._add_lock_button()
            GLib.idle_add(self._centre_lock_button)
        elif not edit and self.lock_btn is not None:
            self.canvas.remove(self.lock_btn)
            self.lock_btn = None
        self._apply_input_region()

    def _on_lock(self, _btn):
        self.set_edit(False)
        if self._on_edit is not None:
            # Through the settings service, so the hover column is rebuilt out
            # of layout mode too — it decides drag-vs-click-through once, at
            # construction.
            self._on_edit(False)
        else:
            update_config_values({"overlay_edit": False})

    # -- input region: the gear only when locked, panels in edit ---------
    def _gear_rect(self) -> tuple[int, int, int, int] | None:
        if self.gear_btn is None or not self.gear_btn.get_visible():
            return None
        _, nat = self.gear_btn.get_preferred_size()
        x, y = self._gear_pos
        return (x, y, max(nat.width, 1), max(nat.height, 1))

    def _mmr_rect(self) -> tuple[int, int, int, int] | None:
        if self.mmr_btn is None or not self.mmr_btn.get_visible():
            return None
        _, nat = self.mmr_btn.get_preferred_size()
        x, y = self._mmr_pos
        return (x, y, max(nat.width, 1), max(nat.height, 1))

    def _input_rects(self) -> list[tuple[int, int, int, int]]:
        """Every rect that accepts a click instead of passing it to the game.

        Locked, that is the gear, plus the MMR nudge for the couple of minutes
        it is up after a game ends. Each rect here is a hole in the
        click-through guarantee that keeps Hearthstone's own
        board-preview-on-hover working, so the list stays as short as it can be.
        """
        gear = self._gear_rect()
        nudge = self._mmr_rect()
        if not self.edit:
            return [r for r in (gear, nudge) if r]
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
        for rect in (gear, nudge):
            if rect:
                rects.append(rect)
        return rects

    def _apply_input_region(self, *_):
        surface = self.get_surface()
        if surface is None:
            return False
        import cairo

        rects = self._input_rects()
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
        if name == "hud":
            self._place_chrome()
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

    def set_mmr_prompt(self, showing: bool) -> None:
        """Raise or drop the end-of-game "Record MMR" nudge.

        Toggling it re-uploads the input region: while it is up the overlay
        swallows clicks over those pixels, and while it is not it must hand
        them straight back to the game.
        """
        if self.mmr_btn is None or self.mmr_btn.get_visible() == showing:
            return
        self.mmr_btn.set_visible(showing)
        if showing:
            self._place_mmr()
        self._apply_input_region()

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
            self.hud.set_lethal(None)

    def set_damage(self, dealt: str | None, taken: str | None) -> None:
        self.hud.set_damage(dealt, taken)

    def set_lethal(self, risk: float | None) -> None:
        self.hud.set_lethal(risk)

    def set_result(self, outcome: str | None, damage: int = 0) -> None:
        self.hud.set_result(outcome, damage)

    def set_buffs(self, entries, shop=(), gold_next_turn=0, free_rerolls=0) -> None:
        """Board buffs, then the Elemental tavern buff, then turn economy.

        `shop` arrives carrying every tribe the reader recognises; the panel
        only ever draws the Elemental row out of it, because a minion sitting
        in Bob's tavern is a different quantity from one already on your
        board — flattened into one list the two read as a single stacking
        number.
        """
        for row in self._buff_rows:
            self.buffs.remove(row)
        self._buff_rows.clear()
        for label, atk, hp in entries:
            self._buff_rows.append(self._buff_chip(label, f"+{atk}/+{hp}"))
        shop = tuple(b for b in shop if b[0] == "Elemental")
        if shop:
            self._buff_rows.append(self._buff_group("In Bob's Tavern"))
            for label, atk, hp in shop:
                self._buff_rows.append(self._buff_chip(label, f"+{atk}/+{hp}"))
        if gold_next_turn or free_rerolls:
            self._buff_rows.append(self._buff_group("This Turn"))
            if gold_next_turn:
                sign = "+" if gold_next_turn > 0 else ""
                self._buff_rows.append(
                    self._buff_chip("Gold Next Turn", f"{sign}{gold_next_turn}")
                )
            if free_rerolls:
                self._buff_rows.append(
                    self._buff_chip("Free Reroll", f"{free_rerolls} available")
                )
        for row in self._buff_rows:
            self.buffs.append(row)
        self._set_content("buffs", bool(self._buff_rows))

    def _buff_group(self, title: str) -> Gtk.Widget:
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("buff-group")
        return label

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

    def set_board(self, title: str, subtitle: str = "", board=None) -> None:
        self.board.show_board(title, subtitle, board)
        self._set_content("board", True)

    def clear_board(self) -> None:
        self.board.clear()
        self._set_content("board", False)

    def set_hover_board(self, title: str, subtitle: str = "", board=None,
                        hero_card_id: str | None = None, dead: bool = False,
                        forecast: str | None = None) -> None:
        self.hover_board.show_board(title, subtitle, board, hero_card_id, dead)
        self.hover_board.set_odds(forecast)
        self._set_content("hover", True)

    def clear_hover_board(self) -> None:
        self.hover_board.clear()
        self._set_content("hover", False)

    def set_standings(self, standings) -> None:
        self.rail.set_standings(standings)
        self._set_content("rail", bool(standings))

    def set_next_opponent(self, player_id: int | None) -> None:
        self.rail.set_next_opponent(player_id)

    def set_hot_place(self, place: int | None) -> None:
        self.rail.set_hot(place)
