"""RoundArt: a portrait must never show a hero other than the one it was given."""

from __future__ import annotations

import asyncio

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from bgtracker.overlay import widgets  # noqa: E402

A, B = "HERO_A", "HERO_B"


@pytest.fixture
def art(monkeypatch, tmp_path):
    """A is cached; B only arrives when the test releases its fetch."""
    release = asyncio.Event()

    async def fetch(card_id, kind="crop"):
        await release.wait()
        return tmp_path / f"{card_id}.png"

    monkeypatch.setattr(widgets.art, "cached_art",
                        lambda cid, kind="crop": tmp_path / "a.png" if cid == A else None)
    monkeypatch.setattr(widgets.art, "fetch_art", fetch)
    loaded: list[str] = []
    monkeypatch.setattr(widgets.RoundArt, "_set_texture",
                        lambda self, path: (loaded.append(path.name),
                                            setattr(self, "_texture", object())))
    return release, loaded


def test_changing_card_drops_the_previous_face_until_the_new_one_loads(art):
    async def run():
        portrait = widgets.RoundArt(40)
        portrait.load(A)
        assert portrait._texture is not None
        portrait.load(B)            # not cached: fetch is in flight
        assert portrait._texture is None, "A's face stayed up beside B's row"

    asyncio.run(run())


def test_a_late_fetch_does_not_overwrite_a_newer_card(art):
    release, loaded = art

    async def run():
        portrait = widgets.RoundArt(40)
        portrait.load(B)            # slow fetch starts
        portrait.load(A)            # moved on; A drawn from cache
        release.set()
        await portrait._task
        assert loaded == ["a.png"], "B's late fetch overwrote A"

    asyncio.run(run())


def test_losing_the_card_clears_the_face(art):
    async def run():
        portrait = widgets.RoundArt(40)
        portrait.load(A)
        portrait.load(None)
        assert portrait._texture is None

    asyncio.run(run())


def _standing(place, player_id, dead=False):
    from bgtracker.parse.events import Standing

    return Standing(place=place, player_id=player_id, hero_card_id=None, health=20, dead=dead)


def _ringed(rail) -> list[int]:
    return [row.player_id for row in rail._rows if row.orb.has_css_class("next")]


def test_the_next_opponent_ring_follows_the_player_across_a_reorder():
    """Rows are keyed by place; the ring is keyed by PLAYER_ID."""
    from bgtracker.overlay.rail import LeaderboardRail

    rail = LeaderboardRail()
    rail.set_standings((_standing(1, 4), _standing(2, 5), _standing(3, 6)))
    rail.set_next_opponent(5)
    assert _ringed(rail) == [5]
    rail.set_standings((_standing(1, 5), _standing(2, 4), _standing(3, 6)))
    assert _ringed(rail) == [5]
    rail.set_next_opponent(None)
    assert _ringed(rail) == []


def test_a_dead_player_is_never_ringed():
    from bgtracker.overlay.rail import LeaderboardRail

    rail = LeaderboardRail()
    rail.set_standings((_standing(1, 4), _standing(8, 5, dead=True)))
    rail.set_next_opponent(5)
    assert _ringed(rail) == []
