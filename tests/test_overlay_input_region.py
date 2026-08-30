"""Which parts of the overlay swallow a click.

The overlay is click-through so Hearthstone keeps its own board-preview-on-hover;
every rect in the input region is a hole in that. While the layout is locked
only two can exist — the settings gear, and the "Record MMR" nudge for the
couple of minutes after a game ends — so it is worth a test that the list stays
that short, and that the rects are small.

No display: `_input_rects` is pure geometry over the window's own bookkeeping, so
the window is built with `__new__` and its fields set directly.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
pytest.importorskip("gi.repository.Gtk4LayerShell")

from bgtracker.overlay.window import GRAB_MIN_H, GRAB_MIN_W, OverlayWindow  # noqa: E402


class FakeSize:
    def __init__(self, width, height):
        self.width, self.height = width, height


class FakeWidget:
    """Stands in for a panel or button: a size and a visibility flag."""

    def __init__(self, width=200, height=100, visible=True):
        self._size = FakeSize(width, height)
        self._visible = visible

    def get_preferred_size(self):
        return None, self._size

    def get_visible(self):
        return self._visible


def _window(edit=False, gear=True, lock=False, nudge=False) -> OverlayWindow:
    win = OverlayWindow.__new__(OverlayWindow)
    win.edit = edit
    win._panels = {"hud": FakeWidget(300, 200), "board": FakeWidget(400, 150)}
    win._pos = {"hud": [1000, 100], "board": [500, 50]}
    win.gear_btn = FakeWidget(22, 22) if gear else None
    win._gear_pos = (1278, 74)
    # Built whenever there is somewhere to send the click, but only *visible*
    # between a game ending and the nudge being taken or timing out.
    win.mmr_btn = FakeWidget(110, 24, visible=nudge)
    win._mmr_pos = (1190, 306)
    win.lock_btn = FakeWidget(180, 40) if lock else None
    win._lock_pos = (900, 1300)
    return win


def test_locked_overlay_exposes_only_the_gear():
    rects = _window().  _input_rects()
    assert rects == [(1278, 74, 22, 22)]


def test_the_gear_is_small():
    """It sits over the game. A big one would eat real estate you click on."""
    (_x, _y, width, height), = _window()._input_rects()
    assert width <= 32 and height <= 32


def test_without_a_gear_the_overlay_is_entirely_click_through():
    # The pre-gear behaviour, still reachable when no settings callback is given.
    assert _window(gear=False)._input_rects() == []


def test_the_mmr_nudge_adds_exactly_one_rect_while_it_is_up():
    assert _window(nudge=True)._input_rects() == [
        (1278, 74, 22, 22), (1190, 306, 110, 24),
    ]


def test_a_hidden_mmr_nudge_costs_nothing():
    """Most of a session it is down, and then it must not exist at all: those
    pixels sit over the game and every one of them has to reach it."""
    assert _window(nudge=False)._input_rects() == [(1278, 74, 22, 22)]


def test_an_overlay_with_no_nudge_at_all_still_works():
    """No on_mmr callback means no button was ever built."""
    win = _window()
    win.mmr_btn = None
    assert win._input_rects() == [(1278, 74, 22, 22)]


def test_layout_mode_exposes_every_visible_panel():
    rects = _window(edit=True, lock=True)._input_rects()
    # Two panels, the Lock button, and the gear.
    assert len(rects) == 4
    assert (1278, 74, 22, 22) in rects, "the gear stays reachable in layout mode"


def test_an_invisible_panel_is_not_grabbable():
    win = _window(edit=True)
    win._panels["board"]._visible = False
    assert len(win._input_rects()) == 2   # hud + gear


def test_an_empty_panel_still_gets_a_body_to_grab():
    """A panel with no content is barely a grip's worth of pixels."""
    win = _window(edit=True)
    win._panels["hud"] = FakeWidget(10, 6)
    win._pos["hud"] = [0, 0]
    hud_rect = win._input_rects()[0]
    assert hud_rect[2] >= GRAB_MIN_W and hud_rect[3] >= GRAB_MIN_H


def test_panels_are_padded_but_the_gear_is_not():
    """Padding a 22px target would double it, for no gain."""
    win = _window(edit=True)
    panel = win._input_rects()[0]
    assert panel[2] > 300, "panels get a margin of error for grabbing"
    assert (1278, 74, 22, 22) in win._input_rects()


def test_window_constructs_for_real():
    """__init__ must run end to end — the __new__-based tests above cannot see
    attribute-order bugs (e.g. _set_content reaching gear_btn before it exists),
    which take the whole overlay down at launch."""
    from gi.repository import Gtk
    from gi.repository import Gtk4LayerShell as LayerShell

    if not LayerShell.is_supported():
        pytest.skip("no layer-shell compositor (headless run)")
    if not Gtk.init_check():
        pytest.skip("no display")
    win = OverlayWindow(
        application=None, on_settings=lambda: None, on_mmr=lambda: None
    )
    try:
        assert win.gear_btn is not None
        assert win.mmr_btn is not None
        assert not win.mmr_btn.get_visible(), "the nudge starts down"
    finally:
        win.destroy()
