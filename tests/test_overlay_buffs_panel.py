"""The Buffs panel renders two groups that mean different things.

The played-buff rows are already baked into the board; the shop rows are what a
minion in Bob's tavern is carrying before you buy it. Read as one flat list they
look like one stacking number, so the shop group gets its own heading.

No display: `set_buffs` only appends to a Gtk.Box, so the window is built with
`__new__` and the handful of fields it touches are set directly.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
pytest.importorskip("gi.repository.Gtk4LayerShell")
from gi.repository import Gtk  # noqa: E402

from bgtracker.overlay import theme  # noqa: E402
from bgtracker.overlay.window import OverlayWindow  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def gtk():
    theme.register_fonts()
    Gtk.init()


@pytest.fixture
def win():
    w = OverlayWindow.__new__(OverlayWindow)
    w.scale = 1.0
    w.buffs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    w._buff_rows = []
    w.shown = {}
    w._set_content = lambda name, has: w.shown.__setitem__(name, has)
    return w


def rows(win) -> list[str]:
    """Each rendered row flattened to text, in order."""
    out = []
    for row in win._buff_rows:
        if isinstance(row, Gtk.Label):
            out.append(row.get_text())
            continue
        parts = [
            child.get_text()
            for child in row
            if isinstance(child, Gtk.Label)
        ]
        out.append(" ".join(parts))
    return out


def test_shop_buffs_are_rendered_under_their_own_heading(win):
    win.set_buffs((("Blood Gem", 2, 2),), (), (("Elemental", 27, 27),))
    assert rows(win) == [
        "Blood Gem +2/+2",
        "In Bob's Tavern",
        "Elemental +27/+27",
    ]


def test_no_heading_when_there_are_no_shop_buffs(win):
    win.set_buffs((("Blood Gem", 2, 2),), (), ())
    assert rows(win) == ["Blood Gem +2/+2"]


def test_shop_buffs_alone_still_show_the_panel(win):
    win.set_buffs((), (), (("All minions", 1, 1),))
    assert rows(win) == ["In Bob's Tavern", "All minions +1/+1"]
    assert win.shown["buffs"] is True


def test_an_empty_panel_is_hidden(win):
    win.set_buffs((), (), ())
    assert win.shown["buffs"] is False


def test_a_repaint_does_not_stack_rows_on_the_previous_ones(win):
    win.set_buffs((), (), (("Elemental", 1, 1),))
    win.set_buffs((), (), (("Elemental", 2, 2),))
    assert rows(win) == ["In Bob's Tavern", "Elemental +2/+2"]
