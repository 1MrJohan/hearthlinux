"""The lobby's tribes, read off what Bob offers.

No tag names the lobby's tribes, so a tribe is confirmed the first time Bob
puts a single-tribe pool minion of it in the tavern. Every exclusion here is
one that would otherwise put a tribe on screen that the lobby may not have:
a dual-tribe minion proves neither tribe, an effect-made offer is not a pool
draw, and our own board is not Bob's. See
docs/superpowers/specs/2026-10-12-lobby-tribes-panel-design.md.
"""

from __future__ import annotations

import pytest

from bgtracker.data import art, cards
from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor
from tests.synthetic import recruit_phase_game

FAKE_DB = {
    "BG_BEAST": {"id": "BG_BEAST", "races": ["BEAST"]},
    "BG_BEAST2": {"id": "BG_BEAST2", "races": ["BEAST"]},
    "BG_MECH": {"id": "BG_MECH", "races": ["MECHANICAL"]},
    "BG_DUAL": {"id": "BG_DUAL", "races": ["DEMON", "NAGA"]},
    "BG_ALL": {"id": "BG_ALL", "races": ["ALL"]},
    "BG_OLD": {"id": "BG_OLD", "race": "MURLOC"},
    "BG_NONE": {"id": "BG_NONE"},
}

BOB = 2  # recruit_phase_game: we are controller 1


@pytest.fixture(autouse=True)
def card_db(monkeypatch):
    monkeypatch.setattr(cards, "_by_id", FAKE_DB)


def offer(b, eid, card_id, *, controller=BOB, pool=1, zone="PLAY"):
    return b.entity(eid, card_id, CARDTYPE="MINION", ZONE=zone, CONTROLLER=controller,
                    IS_BACON_POOL_MINION=pool, ATK=1, HEALTH=1, ZONE_POSITION=1)


def tribe_events(b) -> list[tuple[str, ...]]:
    # A trailing packet, so the last real one is exported.
    b.tag_change("GameEntity", "TURN", 2)
    return [e.tribes for e in LiveGameProcessor().feed(b.lines) if isinstance(e, ev.LobbyTribes)]


def test_a_single_tribe_offer_confirms_its_tribe():
    b = recruit_phase_game()
    offer(b, 20, "BG_BEAST")
    offer(b, 21, "BG_MECH")
    assert tribe_events(b) == [("BEAST",), ("BEAST", "MECHANICAL")]


def test_a_tribe_already_confirmed_emits_nothing_new():
    b = recruit_phase_game()
    offer(b, 20, "BG_BEAST")
    offer(b, 21, "BG_BEAST2")
    offer(b, 22, "BG_BEAST")
    assert tribe_events(b) == [("BEAST",)]


def test_a_card_with_only_the_singular_race_field_still_counts():
    b = recruit_phase_game()
    offer(b, 20, "BG_OLD")
    assert tribe_events(b) == [("MURLOC",)]


@pytest.mark.parametrize("card_id", ["BG_DUAL", "BG_ALL", "BG_NONE", "UNKNOWN_ID"])
def test_offers_that_prove_no_single_tribe_are_ignored(card_id):
    """A dual-tribe minion may need only one of its tribes in the lobby."""
    b = recruit_phase_game()
    offer(b, 20, card_id)
    assert tribe_events(b) == []


def test_an_offer_that_is_not_a_pool_draw_is_ignored():
    """`BG36_360t*` and friends: put in the tavern by an effect."""
    b = recruit_phase_game()
    offer(b, 20, "BG_BEAST", pool=0)
    assert tribe_events(b) == []


def test_our_own_minions_are_not_offers():
    b = recruit_phase_game()
    offer(b, 20, "BG_BEAST", controller=1)
    assert tribe_events(b) == []


def test_minions_in_play_during_combat_are_not_offers():
    """The opponent's combat board is in PLAY too, and it is not Bob's."""
    b = recruit_phase_game()
    b.tag_change("GameEntity", "BOARD_VISUAL_STATE", 2)
    offer(b, 20, "BG_BEAST")
    assert tribe_events(b) == []


def test_an_offer_moving_into_play_later_counts():
    b = recruit_phase_game()
    offer(b, 20, "BG_BEAST", zone="SETASIDE")
    b.tag_change(20, "ZONE", "PLAY")
    assert tribe_events(b) == [("BEAST",)]


def test_an_offer_revealed_by_show_entity_counts():
    """The card id can arrive after the zone, on a SHOW_ENTITY."""
    b = recruit_phase_game()
    b.add("FULL_ENTITY - Creating ID=20 CardID=")
    for tag_name, value in (("CARDTYPE", "MINION"), ("ZONE", "PLAY"),
                            ("CONTROLLER", BOB), ("IS_BACON_POOL_MINION", 1)):
        b.add(f"tag={tag_name} value={value}", indent=1)
    b.add("SHOW_ENTITY - Updating Entity=20 CardID=BG_MECH")
    for tag_name, value in (("CARDTYPE", "MINION"), ("ZONE", "PLAY"),
                            ("CONTROLLER", BOB), ("IS_BACON_POOL_MINION", 1)):
        b.add(f"tag={tag_name} value={value}", indent=1)
    assert tribe_events(b) == [("MECHANICAL",)]


def test_each_game_starts_with_no_tribes():
    b = recruit_phase_game()
    offer(b, 20, "BG_BEAST")
    b.tag_change("GameEntity", "TURN", 2)  # a game's last packet waits for a successor
    second = recruit_phase_game()
    offer(second, 20, "BG_MECH")
    b.lines.extend(second.lines)
    assert tribe_events(b) == [("BEAST",), ("MECHANICAL",)]


# -- names and icons ----------------------------------------------------
def test_every_tribe_has_a_display_name_and_icon_file():
    assert cards.tribe_name("MECHANICAL") == "Mech"
    assert cards.tribe_name("QUILBOAR") == "Quilboar"
    for tribe in cards.TRIBE_NAMES:
        slug = cards.tribe_name(tribe).lower()
        (url,) = art._urls(slug, "tribe")
        assert url.endswith(f"/TribeIcons/{slug}.jpg")
        assert art.art_path(slug, "tribe") == art.TRIBE_DIR / f"{slug}.jpg"


# -- overlay ------------------------------------------------------------
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
pytest.importorskip("gi.repository.Gtk4LayerShell")
from gi.repository import Gtk  # noqa: E402

from bgtracker.overlay import theme  # noqa: E402
from bgtracker.overlay.model import OverlayState, render  # noqa: E402
from bgtracker.overlay.window import OverlayWindow  # noqa: E402
from tests.test_overlay_state import RecordingWindow, _app  # noqa: E402


def test_the_overlay_shows_tribes_and_drops_them_at_the_next_game():
    app, _ = _app()
    app.on_event(ev.LobbyTribes(tribes=("BEAST", "MECHANICAL")), None)
    assert app.state.tribes == ("BEAST", "MECHANICAL")
    app.on_event(ev.GameStart(), None)
    assert app.state.tribes == ()


def test_render_pushes_tribes_only_when_they_change():
    win = RecordingWindow()
    before = OverlayState()
    after = OverlayState(tribes=("BEAST",))
    render(win, after, previous=before)
    assert win.last("set_tribes") == (("BEAST",),)
    win.calls.clear()
    render(win, after, previous=after.snapshot())
    assert "set_tribes" not in win.names()


@pytest.fixture
def win(monkeypatch):
    theme.register_fonts()
    Gtk.init()
    # No loop: RoundArt.load stays on its backdrop instead of fetching.
    monkeypatch.setattr(art, "cached_art", lambda *_a, **_k: None)
    w = OverlayWindow.__new__(OverlayWindow)
    w.scale = 1.0
    w.tribes = Gtk.Box()
    w._tribe_icons = []
    w.shown = {}
    w._set_content = lambda name, has: w.shown.__setitem__(name, has)
    return w


def test_the_panel_draws_one_icon_per_tribe_and_hides_when_empty(win):
    win.set_tribes(("BEAST", "MECHANICAL", "UNDEAD"))
    assert len(win._tribe_icons) == 3
    assert [i._card_id for i in win._tribe_icons] == ["beast", "mech", "undead"]
    assert all(i._kind == "tribe" for i in win._tribe_icons)
    assert win.shown["tribes"] is True
    # A repaint replaces the icons rather than stacking them.
    win.set_tribes(("BEAST",))
    assert len(list(win.tribes)) == 1
    win.set_tribes(())
    assert win.shown["tribes"] is False
