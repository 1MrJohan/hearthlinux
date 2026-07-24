from hearthstone.enums import GameTag

from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor

from .synthetic import minimal_bg_game


# Emitted whenever their underlying state moves, so how many arrive is a
# property of the log rather than of the game's shape. The sequence
# assertions below are about phase order; these are covered directly.
_BOOKKEEPING = {"Standings", "ShopBoard"}


def feed_all(lines):
    proc = LiveGameProcessor()
    out = proc.feed(lines)
    return proc, out


def test_minimal_game_event_sequence():
    _, events = feed_all(minimal_bg_game().lines)
    kinds = [type(e).__name__ for e in events if type(e).__name__ not in _BOOKKEEPING]
    assert kinds == [
        "GameStart",
        "HeroPicked",
        "TurnChange",
        "CombatStart",
        "CombatEnd",
        "GameEnd",
    ]


def test_the_shop_board_is_reported_for_live_odds():
    """The recruit-phase forecast needs the board as it is being built."""
    _, events = feed_all(minimal_bg_game().lines)
    shop = [e for e in events if isinstance(e, ev.ShopBoard)]
    assert shop, "no ShopBoard emitted during the recruit phase"
    [mine] = shop[-1].board.minions
    assert (mine.card_id, mine.attack, mine.health) == ("BG_EX1_506", 2, 3)


def test_an_unchanged_shop_board_is_not_re_reported():
    """Every one of these starts a simulation, so a repeat tag write that
    leaves the board identical must not trigger one."""
    proc, _ = feed_all(minimal_bg_game().lines)
    exporter = proc.current_exporter
    before = exporter._shop_board
    exporter._shop_dirty = True
    emitted: list = []
    exporter._emit = emitted.append
    exporter.maybe_emit_shop_board()
    assert emitted == []
    assert exporter._shop_board == before


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
    kinds = [type(e).__name__ for e in events if type(e).__name__ not in _BOOKKEEPING]
    assert kinds == [
        "GameStart",
        "HeroPicked",
        "TurnChange",
        "CombatStart",
        "CombatEnd",
        "GameEnd",
    ]


def test_standings_carry_rail_fields():
    """The leaderboard rail renders straight off these fields."""
    _, events = feed_all(minimal_bg_game().lines)
    standings = [e for e in events if isinstance(e, ev.Standings)]
    assert standings, "no Standings emitted"
    [entry] = standings[-1].places
    assert entry.place == 3
    assert entry.hero_card_id == "TB_BaconShop_HERO_11"
    assert entry.health == 40 and entry.total_health == 40
    assert entry.you is True      # the synthetic game is played from our seat
    assert entry.dead is False


def test_hero_hp_change_refreshes_standings():
    """HP moves far more often than place; the rail must not show stale HP."""
    game = minimal_bg_game()
    proc = LiveGameProcessor()
    proc.feed(game.lines)
    exporter = proc.current_exporter
    before = exporter._standings[0].health

    # Damage the friendly hero the way a lost combat would.
    hero = next(
        e for e in exporter.game.entities
        if getattr(e, "card_id", None) == "TB_BaconShop_HERO_11"
    )
    hero.tags[GameTag.DAMAGE] = 7
    exporter._standings_dirty = True
    exporter.maybe_emit_standings()

    assert exporter._standings[0].health == before - 7


def test_two_games_in_one_batch():
    """A single feed() batch spanning two games must parse both cleanly
    (fresh parser per CREATE_GAME; every track advanced)."""
    lines = minimal_bg_game().lines + minimal_bg_game().lines
    proc = LiveGameProcessor()
    events = proc.feed(lines)
    kinds = [type(e).__name__ for e in events if type(e).__name__ not in _BOOKKEEPING]
    assert kinds.count("GameStart") == 2
    assert kinds.count("GameEnd") == 2
    assert kinds.count("CombatStart") == 2
