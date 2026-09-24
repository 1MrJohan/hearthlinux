from hearthstone.enums import GameTag

from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor

from .synthetic import LogBuilder, minimal_bg_game, secret_bg_game


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


def test_shop_board_bursts_publish_only_the_final_projection():
    """GTK and the debounce can only use the final state from one tail read;
    projecting the full entity tree after each packet is pure catch-up churn."""
    b = minimal_bg_game()
    b.tag_change(7, "ATK", 8)
    b.tag_change(7, "ATK", 11)
    proc = LiveGameProcessor()
    shops = [e for e in proc.feed(b.lines) if isinstance(e, ev.ShopBoard)]
    assert len(shops) == 1
    assert shops[0].board.minions[0].attack == 11


def test_buffs_bursts_publish_only_the_final_values():
    b = minimal_bg_game()
    b.tag_change(2, "BACON_BLOODGEMBUFFATKVALUE", 2)
    b.tag_change(2, "BACON_BLOODGEMBUFFATKVALUE", 5)
    buffs = [e for e in LiveGameProcessor().feed(b.lines) if isinstance(e, ev.Buffs)]
    assert len(buffs) == 1
    assert buffs[0].entries == (("Blood Gem", 5, 0),)


def test_standings_bursts_publish_only_the_final_health():
    b = minimal_bg_game()
    b.tag_change(4, "DAMAGE", 3)
    b.tag_change(4, "DAMAGE", 7)
    standings = [e for e in LiveGameProcessor().feed(b.lines) if isinstance(e, ev.Standings)]
    assert len(standings) == 1
    [mine] = standings[0].places
    assert mine.health == 33


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


def test_combat_snapshot_retains_opponent_secret_materialized_before_board():
    """Pack Tactics was visible in GameState but used to be discarded.

    The resulting stat-only simulation reported a 100% win for a combat that
    actually lost when the secret summoned its copy.
    """
    _, events = feed_all(secret_bg_game().lines)
    combat = next(e for e in events if isinstance(e, ev.CombatStart))

    [secret] = combat.snapshot.opponent.secrets
    assert (secret.card_id, secret.entity_id) == ("TB_Bacon_Secrets_15", 8)


def test_hero_and_placement():
    _, events = feed_all(minimal_bg_game().lines)
    hero = next(e for e in events if isinstance(e, ev.HeroPicked))
    assert hero.card_id == "TB_BaconShop_HERO_11"
    end = next(e for e in events if isinstance(e, ev.GameEnd))
    assert end.placement == 3


def test_placement_follows_the_player_hero_not_the_late_copy():
    """Every real game since at least 2026-09-22 ends with a newer copy of our
    hero in SETASIDE carrying a stale, better place. The player entity's
    HERO_ENTITY names the real one (9 of 17 retained games were misrecorded)."""
    b = minimal_bg_game()
    b.lines.pop()   # hold STATE=COMPLETE until the copy exists
    b.tag_change(2, "CONTROLLER", 1)   # as real player entities carry
    b.tag_change(2, "HERO_ENTITY", 4)
    b.entity(20, "TB_BaconShop_HERO_11", CARDTYPE="HERO", ZONE="SETASIDE",
             CONTROLLER=1, LINKED_ENTITY=2)
    b.tag_change(20, "PLAYER_LEADERBOARD_PLACE", 1)
    b.tag_change("GameEntity", "STATE", "COMPLETE")
    _, events = feed_all(b.lines)
    end = next(e for e in events if isinstance(e, ev.GameEnd))
    assert end.placement == 3


# -- early concede -------------------------------------------------------
# An early concede never logs STATE=COMPLETE. The only mark it leaves on both
# captured concedes is tag 3479 on our own player entity, and the final place
# lands *after* it. See docs/superpowers/specs/2026-09-23-early-concede-design.md.

def _mid_game() -> LogBuilder:
    """The minimal game, still running: we sit 4th, the opponent 2nd."""
    b = minimal_bg_game()
    del b.lines[-2:]   # no final place, no COMPLETE
    for player, controller, hero in ((2, 1, 4), (3, 2, 5)):
        b.tag_change(player, "CONTROLLER", controller)   # as real player entities carry
        b.tag_change(player, "HERO_ENTITY", hero)
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 4)
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 2)
    return b


def _ends(lines):
    return [e for e in feed_all(lines)[1] if isinstance(e, ev.GameEnd)]


def test_nothing_ends_a_game_still_running():
    assert _ends(_mid_game().lines) == []


def test_a_concede_ends_on_our_new_place_without_complete():
    """Game 409: 3479, then our place 4 → 8, then silence."""
    b = _mid_game()
    b.tag_change(2, "3479", 1)
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 8)
    assert _ends(b.lines) == [ev.GameEnd(placement=8)]


def test_the_marker_alone_does_not_end_the_game():
    """Ending on 3479 itself would record the pre-concede place (4th) as
    final — in game 409 the 8 arrived after it."""
    b = _mid_game()
    b.tag_change(2, "3479", 1)
    assert _ends(b.lines) == []


def test_the_lobby_reshuffling_around_a_concede_is_not_our_place():
    b = _mid_game()
    b.tag_change(2, "3479", 1)
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 1)
    assert _ends(b.lines) == []
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 8)
    assert _ends(b.lines) == [ev.GameEnd(placement=8)]


def test_a_concede_that_completes_ends_once_at_its_place():
    """Game 411: already at its final place, then COMPLETE."""
    b = _mid_game()
    b.tag_change(2, "PLAYSTATE", "CONCEDED")
    b.tag_change(2, "3479", 1)
    b.tag_change("GameEntity", "STATE", "COMPLETE")
    assert _ends(b.lines) == [ev.GameEnd(placement=4)]


def test_an_armed_game_ends_at_the_next_game_with_its_place():
    b = _mid_game()
    b.tag_change(2, "3479", 1)
    b.add("CREATE_GAME")
    assert _ends(b.lines) == [ev.GameEnd(placement=4)]


def test_an_unarmed_unfinished_game_still_ends_with_nothing():
    """Without a concede, the live place is not a final one."""
    b = _mid_game()
    b.add("CREATE_GAME")
    assert _ends(b.lines) == []


def test_an_opponents_concede_is_not_ours():
    b = _mid_game()
    b.tag_change(3, "3479", 1)
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 8)
    assert _ends(b.lines) == []


def test_the_game_entity_tag_alone_ends_nothing():
    b = _mid_game()
    b.tag_change("GameEntity", "4302", 1)
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 8)
    assert _ends(b.lines) == []


def test_bob_skins_are_never_counted_as_player_heroes():
    """Bob is the tavern keeper, not a player, and sits in PLAY as a HERO in
    every real game wearing one of 50+ cosmetic skins. The scan filters him by
    prefix, so a skin the code has never seen is still excluded — this guards
    that prefix match against being weakened to an exact-id compare."""
    b = LogBuilder()
    b.add("CREATE_GAME")
    b.add("GameEntity EntityID=1", indent=1)
    b.add("tag=TURN value=1", indent=2)
    b.add("Player EntityID=2 PlayerID=1 GameAccountId=[hi=1 lo=1]", indent=1)
    b.add("Player EntityID=3 PlayerID=2 GameAccountId=[hi=1 lo=2]", indent=1)
    b.entity(4, "TB_BaconShop_HERO_11", CARDTYPE="HERO", ZONE="PLAY", CONTROLLER=1,
             HEALTH=40, PLAYER_TECH_LEVEL=2)
    b.entity(5, "TB_BaconShop_HERO_22", CARDTYPE="HERO", ZONE="PLAY", CONTROLLER=2,
             HEALTH=30, PLAYER_TECH_LEVEL=1)
    b.entity(99, "TB_BaconShopBob_SKIN_Z", CARDTYPE="HERO", ZONE="PLAY", CONTROLLER=14)

    proc = LiveGameProcessor()
    proc.feed(b.lines)
    ids = {h.card_id for h in proc.current_exporter._heroes_in_play()}
    assert ids == {"TB_BaconShop_HERO_11", "TB_BaconShop_HERO_22"}


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
    assert entry.tier == 2
    assert entry.you is True      # the synthetic game is played from our seat
    assert entry.dead is False


def test_missing_tavern_tier_stays_unknown():
    lines = [
        line for line in minimal_bg_game().lines
        if "tag=PLAYER_TECH_LEVEL value=2" not in line
    ]
    _, events = feed_all(lines)
    assert _last_standings(events)[3].tier is None


def test_out_of_range_tavern_tier_stays_unknown():
    game = minimal_bg_game()
    game.tag_change(4, "PLAYER_TECH_LEVEL", 7)
    game.tag_change("GameEntity", "TURN", 4)  # export the preceding packet
    _, events = feed_all(game.lines)
    assert _last_standings(events)[3].tier is None


def _last_standings(events) -> dict[int, ev.Standing]:
    standings = [e for e in events if isinstance(e, ev.Standings)]
    assert standings, "no Standings emitted"
    return {s.place: s for s in standings[-1].places}


def test_alive_opponent_in_setaside_is_not_dead():
    """Opponents' heroes rest in SETASIDE except while paired against us;
    only GRAVEYARD or hp<=0 means eliminated."""
    b = minimal_bg_game()
    b.tag_change(5, "PLAYER_ID", 2)
    b.tag_change(5, "ZONE", "SETASIDE")
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 2)
    _, events = feed_all(b.lines)
    entry = _last_standings(events)[2]
    assert entry.health == 30
    assert entry.dead is False


def test_hero_at_zero_hp_is_dead_wherever_it_sits():
    b = minimal_bg_game()
    b.tag_change(5, "PLAYER_ID", 2)
    b.tag_change(5, "ZONE", "SETASIDE")
    b.tag_change(5, "DAMAGE", 35)
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 2)
    _, events = feed_all(b.lines)
    assert _last_standings(events)[2].dead is True


def test_hero_in_graveyard_is_dead_even_at_positive_hp():
    b = minimal_bg_game()
    b.tag_change(5, "PLAYER_ID", 2)
    b.tag_change(5, "ZONE", "GRAVEYARD")
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 2)
    _, events = feed_all(b.lines)
    assert _last_standings(events)[2].dead is True


def test_stale_unidentified_hero_copy_cannot_steal_a_place():
    """Real logs grow duplicate hero entities with no PLAYER_ID that linger in
    GRAVEYARD carrying a stale place tag; they must not override the real
    player's row (which would grey out a living player)."""
    b = minimal_bg_game()
    b.tag_change(5, "PLAYER_ID", 2)
    b.tag_change(5, "ZONE", "SETASIDE")
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 2)
    # Newer entity, same place, no PLAYER_ID, dead-looking zone.
    b.entity(11, "TB_BaconShop_HERO_22", CARDTYPE="HERO", ZONE="GRAVEYARD",
             CONTROLLER=2, HEALTH=30)
    b.tag_change(11, "PLAYER_LEADERBOARD_PLACE", 2)
    _, events = feed_all(b.lines)
    entry = _last_standings(events)[2]
    assert entry.player_id == 2
    assert entry.dead is False


def test_elimination_is_sticky_across_ghost_reuse():
    """A Kel'Thuzad ghost fight reuses the dead player's hero entity and can
    reset it to full HP; elimination is permanent, so the rail must not
    resurrect them."""
    b = minimal_bg_game()
    b.tag_change(5, "PLAYER_ID", 2)
    b.tag_change(5, "ZONE", "SETASIDE")
    b.tag_change(5, "DAMAGE", 35)
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 8)
    b.tag_change(5, "DAMAGE", 0)   # ghost reuse: back to full HP
    _, events = feed_all(b.lines)
    entry = _last_standings(events)[8]
    assert entry.health == 30      # live HP still reported honestly
    assert entry.dead is True


def test_combat_hero_copy_reset_is_not_an_elimination():
    """Every fight's hero copy is moved to REMOVEDFROMGAME, zeroed and restored
    in one burst. Latched at the packet, the zero greyed out every opponent on
    the rail after their first fight with us."""
    b = minimal_bg_game()
    b.tag_change(5, "PLAYER_ID", 2)
    b.tag_change(5, "ZONE", "SETASIDE")
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 2)
    b.entity(12, "TB_BaconShop_HERO_22", CARDTYPE="HERO", ZONE="PLAY",
             CONTROLLER=2, HEALTH=30, ARMOR=15, PLAYER_ID=2,
             BACON_COMBAT_PHASE_HERO=1)
    b.tag_change(12, "ZONE", "REMOVEDFROMGAME")
    b.tag_change(12, "HEALTH", 0)
    b.tag_change(12, "ARMOR", 0)
    b.tag_change(12, "HEALTH", 30)
    b.tag_change(12, "ARMOR", 15)
    b.tag_change("GameEntity", "TURN", 4)  # export the preceding packet
    proc, events = feed_all(b.lines)
    assert 2 not in proc.current_exporter._dead_player_ids
    assert _last_standings(events)[2].dead is False


def test_lethal_on_the_combat_copy_reaches_combat_end():
    """The only entity that shows a lethal before the fight ends is the
    opponent's combat copy, which then leaves PLAY — so the end snapshot has
    no opponent, and the elimination has to travel on the event."""
    b = LogBuilder()
    b.add("CREATE_GAME")
    b.add("GameEntity EntityID=1", indent=1)
    b.add("tag=TURN value=1", indent=2)
    b.add("Player EntityID=2 PlayerID=1 GameAccountId=[hi=1 lo=1]", indent=1)
    b.add("Player EntityID=3 PlayerID=2 GameAccountId=[hi=1 lo=2]", indent=1)
    b.entity(4, "TB_BaconShop_HERO_11", CARDTYPE="HERO", ZONE="PLAY", CONTROLLER=1,
             HEALTH=40, PLAYER_ID=1)
    b.entity(6, "BG_EX1_506", CARDTYPE="MINION", ZONE="HAND", CONTROLLER=1)
    b.entity(7, "BG_EX1_506", CARDTYPE="MINION", ZONE="PLAY", CONTROLLER=1,
             ATK=2, HEALTH=3, ZONE_POSITION=1)
    b.tag_change("GameEntity", "TURN", 2)
    b.tag_change("GameEntity", "BOARD_VISUAL_STATE", 2)
    b.entity(12, "TB_BaconShop_HERO_22", CARDTYPE="HERO", ZONE="PLAY",
             CONTROLLER=2, HEALTH=30, PLAYER_ID=2, BACON_COMBAT_PHASE_HERO=1)
    b.entity(8, "BG_CS2_065", CARDTYPE="MINION", ZONE="PLAY", CONTROLLER=2,
             ATK=1, HEALTH=7, ZONE_POSITION=1)
    b.tag_change("GameEntity", "TURN", 3)  # export the preceding packets
    b.tag_change(12, "DAMAGE", 40)
    b.tag_change(12, "ZONE", "GRAVEYARD")
    b.tag_change("GameEntity", "BOARD_VISUAL_STATE", 1)
    b.tag_change("GameEntity", "TURN", 4)
    _, events = feed_all(b.lines)

    [start] = [e for e in events if isinstance(e, ev.CombatStart)]
    [end] = [e for e in events if isinstance(e, ev.CombatEnd)]
    assert start.snapshot.opponent.bg_player_id == 2
    assert end.snapshot.opponent is None
    assert end.eliminated == frozenset({2})


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


def test_tavern_tier_change_refreshes_standings():
    """An upgrade does not necessarily move HP or placement, so its own tag
    change must refresh the rail rather than waiting for an unrelated event."""
    game = minimal_bg_game()
    proc = LiveGameProcessor()
    proc.feed(game.lines)
    assert proc.current_exporter._standings[0].tier == 2

    start = len(game.lines)
    game.tag_change(4, "PLAYER_TECH_LEVEL", 3)
    game.tag_change("GameEntity", "TURN", 4)  # export the preceding packet
    events = proc.feed(game.lines[start:])

    [standings] = [event for event in events if isinstance(event, ev.Standings)]
    assert standings.places[0].tier == 3


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
