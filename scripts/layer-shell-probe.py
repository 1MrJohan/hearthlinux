#!/usr/bin/env python3
"""KWin Wayland overlay smoke test — the Phase 2 gate.

Run me, then click *through* the green panel onto whatever is behind it,
and confirm the panel stays visible over a borderless-windowed game.

    python3 scripts/layer-shell-probe.py

Checklist:
  [ ] transparent window appears top-right with a green panel
  [ ] clicks pass through it to the window underneath
  [ ] it stays on top of a borderless-fullscreen (Wine/XWayland) window
  [ ] Ctrl+C in the terminal closes it cleanly

Requires: gtk4, gtk4-layer-shell, python-gobject (all from Arch repos).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gtk4LayerShell as LayerShell  # noqa: E402

CSS = b"""
.hud {
    background-color: rgba(20, 80, 30, 0.75);
    color: white;
    font-size: 18px;
    border-radius: 12px;
    padding: 16px;
}
"""


def on_activate(app):
    win = Gtk.Window(application=app)
    LayerShell.init_for_window(win)
    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
    LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
    LayerShell.set_anchor(win, LayerShell.Edge.RIGHT, True)
    LayerShell.set_margin(win, LayerShell.Edge.TOP, 40)
    LayerShell.set_margin(win, LayerShell.Edge.RIGHT, 40)
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)

    label = Gtk.Label(label="BG overlay probe\nclicks should pass through me")
    label.add_css_class("hud")
    win.set_child(label)

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        win.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    # Click-through: empty input region on the surface once realized.
    def set_input_region(*_):
        surface = win.get_surface()
        if surface is not None:
            import cairo

            surface.set_input_region(cairo.Region())

    win.connect("realize", set_input_region)
    win.present()
    set_input_region()


app = Gtk.Application(application_id="dev.bgtracker.probe")
app.connect("activate", on_activate)
app.run()
