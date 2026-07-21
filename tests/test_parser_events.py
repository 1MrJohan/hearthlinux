from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor

from .synthetic import minimal_bg_game


def feed_all(lines):
    proc = LiveGameProcessor()
    out = proc.feed(lines)
    return proc, out


def test_minimal_game_event_sequence():
    _, events = feed_all(minimal_bg_game().lines)
    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "GameStart",
        "HeroPicked",
        "TurnChange",
        "CombatStart",
        "CombatEnd",
        "GameEnd",
    ]


def test_combat_snapshot_contents():
    _, events = feed_all(minimal_bg_game().lines)
    combat = next(e for e in events if isinstance(e, ev.CombatStart))
    snap = combat.snapshot
    assert snap.turn == 1
    assert snap.friendly.hero_card_id == "TB_BaconShop_HERO_11"
    assert snap.friendly.tier == 2
    assert snap.friendly.health == 40
    [mine] = snap.friendly.minions
    assert (mine.card_id, mine.attack, mine.health) == ("BG_EX1_506", 2, 3)
    [theirs] = snap.opponent.minions
    assert theirs.taunt and theirs.health == 7


def test_hero_and_placement():
    _, events = feed_all(minimal_bg_game().lines)
    hero = next(e for e in events if isinstance(e, ev.HeroPicked))
    assert hero.card_id == "TB_BaconShop_HERO_11"
    end = next(e for e in events if isinstance(e, ev.GameEnd))
    assert end.placement == 3


def test_incremental_feed_matches_bulk():
    lines = minimal_bg_game().lines
    proc = LiveGameProcessor()
    events = []
    for line in lines:  # one line at a time, like live tailing
        events.extend(proc.feed([line]))
    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "GameStart",
        "HeroPicked",
        "TurnChange",
        "CombatStart",
        "CombatEnd",
        "GameEnd",
    ]
