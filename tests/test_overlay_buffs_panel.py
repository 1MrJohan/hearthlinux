"""The Buffs panel renders three groups that mean different things.

The played-buff rows are already baked into the board; the tavern-buff row is
what a minion in Bob's tavern is carrying before you buy it; the economy rows
are turn-scoped state that has nothing to do with a minion at all. Read as one
flat list the first two look like a single stacking number, so the shop group
gets its own heading — and `shop` itself arrives carrying every tribe the
reader recognises, curated down to Elemental here because that is what the
panel shows, not what the reader returns.

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
    win.set_buffs((("Blood Gem", 2, 2),), (("Elemental", 27, 27),))
    assert rows(win) == [
        "Blood Gem +2/+2",
        "In Bob's Tavern",
        "Elemental +27/+27",
    ]


def test_no_heading_when_there_are_no_shop_buffs(win):
    win.set_buffs((("Blood Gem", 2, 2),), ())
    assert rows(win) == ["Blood Gem +2/+2"]


def test_shop_buffs_alone_still_show_the_panel(win):
    win.set_buffs((), (("Elemental", 1, 1),))
    assert rows(win) == ["In Bob's Tavern", "Elemental +1/+1"]
    assert win.shown["buffs"] is True


def test_a_non_elemental_shop_tribe_is_curated_out(win):
    """The panel draws only Elemental out of `shop`; every other tribe the
    reader can still recognise is deliberately not shown here."""
    win.set_buffs((), (("Beast", 3, 3),))
    assert rows(win) == []
    assert win.shown["buffs"] is False


def test_an_empty_panel_is_hidden(win):
    win.set_buffs((), ())
    assert win.shown["buffs"] is False


def test_a_repaint_does_not_stack_rows_on_the_previous_ones(win):
    win.set_buffs((), (("Elemental", 1, 1),))
    win.set_buffs((), (("Elemental", 2, 2),))
    assert rows(win) == ["In Bob's Tavern", "Elemental +2/+2"]


def test_gold_next_turn_gets_its_own_heading(win):
    win.set_buffs((), (), 2, 0)
    assert rows(win) == ["This Turn", "Gold Next Turn +2"]


def test_overdrawn_gold_next_turn_shows_the_minus_sign(win):
    win.set_buffs((), (), -1, 0)
    assert rows(win) == ["This Turn", "Gold Next Turn -1"]


def test_free_reroll_gets_its_own_heading(win):
    win.set_buffs((), (), 0, 1)
    assert rows(win) == ["This Turn", "Free Reroll 1 available"]


def test_gold_and_reroll_share_one_this_turn_heading(win):
    win.set_buffs((), (), 2, 1)
    assert rows(win) == [
        "This Turn",
        "Gold Next Turn +2",
        "Free Reroll 1 available",
    ]
    assert win.shown["buffs"] is True


def test_no_this_turn_heading_when_both_are_zero(win):
    win.set_buffs((("Blood Gem", 2, 2),), ())
    assert "This Turn" not in rows(win)
