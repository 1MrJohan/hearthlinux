"""The Buffs panel renders three groups that mean different things.

The played-buff tiles are already baked into the board; the tavern-buff tile
is what a minion in Bob's tavern is carrying before you buy it; the economy
tiles are turn-scoped state that has nothing to do with a minion at all. Run
together, the first two look like a single stacking number, so each group sits
in its own row with a rule between them. `shop` itself arrives carrying every
tribe the reader recognises, curated down to Elemental here because that is
what the panel shows, not what the reader returns.

Each buff is an icon with its value under it and no name, so `rows` reads
back what each tile stands for off `tile.buff`.

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
    """Each group as "Label value" per tile, with "---" for the rule."""
    out = []
    for row in win._buff_rows:
        if isinstance(row, Gtk.Separator):
            out.append("---")
            continue
        tiles = []
        for tile in row:
            (value,) = [c.get_text() for c in tile if isinstance(c, Gtk.Label)]
            tiles.append(f"{tile.buff} {value}")
        out.append(", ".join(tiles))
    return out


def test_shop_buffs_are_rendered_under_their_own_heading(win):
    win.set_buffs((("Blood Gem", 2, 2),), (("Elemental", 27, 27),))
    assert rows(win) == [
        "Blood Gem +2/+2",
        "---",
        "Elemental +27/+27",
    ]


def test_no_rule_when_there_are_no_shop_buffs(win):
    win.set_buffs((("Blood Gem", 2, 2),), ())
    assert rows(win) == ["Blood Gem +2/+2"]


def test_shop_buffs_alone_still_show_the_panel(win):
    win.set_buffs((), (("Elemental", 1, 1),))
    assert rows(win) == ["Elemental +1/+1"]
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
    assert rows(win) == ["Elemental +2/+2"]


def test_gold_next_turn_gets_its_own_group(win):
    win.set_buffs((("Blood Gem", 1, 1),), (), 2, 0)
    assert rows(win) == ["Blood Gem +1/+1", "---", "Gold Next Turn +2"]


def test_overdrawn_gold_next_turn_shows_the_minus_sign(win):
    win.set_buffs((), (), -1, 0)
    assert rows(win) == ["Gold Next Turn -1"]


def test_free_reroll_shows_the_count(win):
    win.set_buffs((), (), 0, 1)
    assert rows(win) == ["Free Reroll 1"]


def test_gold_and_reroll_share_one_group(win):
    win.set_buffs((), (), 2, 1)
    assert rows(win) == ["Gold Next Turn +2, Free Reroll 1"]
    assert win.shown["buffs"] is True


def test_no_economy_group_when_both_are_zero(win):
    win.set_buffs((("Blood Gem", 2, 2),), ())
    assert rows(win) == ["Blood Gem +2/+2"]


def test_an_attack_only_buff_drops_the_zero_health(win):
    win.set_buffs((("Undead", 4, 0), ("Beetle Army", 12, 8)), ())
    assert rows(win) == ["Undead +4, Beetle Army +12/+8"]


def test_every_tile_is_an_icon_with_no_name(win):
    from bgtracker.overlay.widgets import RoundArt

    win.set_buffs((("Blood Gem", 2, 2), ("Spell Power", 1, 1)), (("Elemental", 3, 3),), 2, 1)
    for row in win._buff_rows:
        for tile in ([] if isinstance(row, Gtk.Separator) else row):
            icon, value = list(tile)
            assert isinstance(icon, RoundArt)
            assert tile.buff not in value.get_text()
    icons = {tile.buff: list(tile)[0] for row in win._buff_rows
             if not isinstance(row, Gtk.Separator) for tile in row}
    assert icons["Elemental"]._kind == "tribe" and icons["Elemental"]._card_id == "elemental"
    assert icons["Free Reroll"]._card_id == "TB_BaconShop_8p_Reroll_Button"
