"""Tavern shop buffs: what a minion in Bob's tavern already carries.

Nomi and friends do not write a player *tag* the way Blood Gem does. They feed
a per-tribe `BG_ShopBuff_<tribe>` enchantment attached to the player entity,
whose running total lives in TAG_SCRIPT_DATA_NUM_1/2. Several cards stack into
one entity, so this is a read of a total rather than a count of triggers.
"""

from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor

from .synthetic import LogBuilder


def shop_buff_game() -> LogBuilder:
    """A recruit phase with the friendly player holding shop buffs."""
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
    # revealed card in OUR hand -> friendly-player detection says player 1
    b.entity(6, "BG_EX1_506", CARDTYPE="MINION", ZONE="HAND", CONTROLLER=1)
    return b


def feed(builder: LogBuilder) -> list:
    """Feed the log, with a trailing packet so the last one is exported.

    A packet is only exported once something follows it — the tailer has to
    assume a final line may still be half-written.
    """
    builder.tag_change("GameEntity", "TURN", 2)
    return LiveGameProcessor().feed(builder.lines)


def buffs_from(builder: LogBuilder) -> ev.Buffs | None:
    emitted = [e for e in feed(builder) if isinstance(e, ev.Buffs)]
    return emitted[-1] if emitted else None


def test_a_shop_buff_enchantment_is_reported_with_its_running_total():
    b = shop_buff_game()
    b.entity(20, "BG_ShopBuff_Elemental", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=27,
             TAG_SCRIPT_DATA_NUM_2=27)
    assert buffs_from(b).shop == (("Elemental", 27, 27),)


def test_the_per_minion_mark_is_not_mistaken_for_the_player_total():
    """`BG_ShopBuff_<tribe>_Ench` sits on the shop minion, not on the player.

    A prefix match would sum every buffed minion in the tavern into the total.
    """
    b = shop_buff_game()
    b.entity(20, "BG_ShopBuff_Elemental", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=4,
             TAG_SCRIPT_DATA_NUM_2=4)
    b.entity(21, "BG_ShopBuff_Elemental_Ench", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=30, TAG_SCRIPT_DATA_NUM_1=4,
             TAG_SCRIPT_DATA_NUM_2=4)
    assert buffs_from(b).shop == (("Elemental", 4, 4),)


def test_an_opponents_shop_buff_is_not_ours():
    b = shop_buff_game()
    b.entity(20, "BG_ShopBuff_Beast", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=2, ATTACHED=3, TAG_SCRIPT_DATA_NUM_1=6,
             TAG_SCRIPT_DATA_NUM_2=6)
    buffs = buffs_from(b)
    assert buffs is None or buffs.shop == ()


def test_a_zero_valued_shop_buff_makes_no_row():
    """The enchantment is created before the first trigger sets its value."""
    b = shop_buff_game()
    b.entity(20, "BG_ShopBuff", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2)
    buffs = buffs_from(b)
    assert buffs is None or buffs.shop == ()


def test_every_tribe_that_can_buff_the_tavern_is_recognised():
    b = shop_buff_game()
    b.entity(20, "BG_ShopBuff", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=1,
             TAG_SCRIPT_DATA_NUM_2=1)
    b.entity(21, "BG_ShopBuff_Quilboar", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=2,
             TAG_SCRIPT_DATA_NUM_2=3)
    b.entity(22, "BG_ShopBuff_MultiRace", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=5,
             TAG_SCRIPT_DATA_NUM_2=5)
    assert buffs_from(b).shop == (
        ("All minions", 1, 1),
        ("Multi-tribe", 5, 5),
        ("Quilboar", 2, 3),
    )


def test_a_shop_buff_that_has_not_moved_is_not_re_reported():
    """Every Buffs event repaints the panel; a no-op tag write must not."""
    b = shop_buff_game()
    b.entity(20, "BG_ShopBuff_Elemental", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=4,
             TAG_SCRIPT_DATA_NUM_2=4)
    b.tag_change(20, "TAG_SCRIPT_DATA_NUM_1", 4)
    with_shop = [e for e in feed(b) if isinstance(e, ev.Buffs) and e.shop]
    assert len(with_shop) == 1
