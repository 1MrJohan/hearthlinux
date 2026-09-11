"""What the log proves about magnetized mechs.

A magnetized minion becomes an enchantment on its host tagged MAGNETIC, so the
*cards* linked to a minion are exactly countable for both players. The number
of mechs is not: repeats of the same card fold into the one enchantment they
created, growing its stats instead of adding a second entity. See
docs/superpowers/specs/2026-08-29-magnetize-count-design.md, including why
counting the growth writes was tried and rejected.
"""

from __future__ import annotations

from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor

from .synthetic import magnetize_fold, magnetized_bg_game


def _shop_board(lines):
    boards = [e for e in LiveGameProcessor().feed(lines) if isinstance(e, ev.ShopBoard)]
    assert boards, "no ShopBoard emitted"
    return boards[-1].board


def _host(board, entity_id=7):
    [minion] = [m for m in board.minions if m.entity_id == entity_id]
    return minion


def test_a_magnetized_mech_is_visible_as_a_magnetic_enchantment():
    minion = _host(_shop_board(magnetized_bg_game().lines))
    [ench] = [e for e in minion.enchantments if e.magnetic]
    assert ench.card_id == "BG31_171te"
    assert minion.linked_cards == 1


def test_an_ordinary_buff_is_not_counted_as_a_linked_mech():
    """Every buff in the game is an enchantment; only magnetized minions carry
    MAGNETIC, and reading the flag is the whole difference."""
    b = magnetized_bg_game()
    b.entity(21, "BG26_146e2", CARDTYPE="ENCHANTMENT", ZONE="PLAY", CONTROLLER=1,
             ATTACHED=7, TAG_SCRIPT_DATA_NUM_1=9, TAG_SCRIPT_DATA_NUM_2=9)
    b.tag_change(7, "ZONE_POSITION", 1)
    b.tag_change("GameEntity", "TURN", 4)
    minion = _host(_shop_board(b.lines))
    assert len(minion.enchantments) == 2
    assert minion.linked_cards == 1


def test_the_magnetized_stats_are_the_running_total():
    """Four 4/4 Satellites are one enchantment at 16/16, not four entities, so
    the stats are what the log states and the count is what it hides."""
    b = magnetized_bg_game()
    for value in (8, 12, 16):
        magnetize_fold(b, 20, 7, value)
    minion = _host(_shop_board(b.lines))
    assert minion.magnetized_stats == (16, 16)
    assert minion.linked_cards == 1, "still one linked card after four magnetizes"


def test_an_opponents_linked_cards_are_readable_too():
    """Their shop never reaches our log, but the enchantments ride along on the
    combat board the same way ours do."""
    b = magnetized_bg_game()
    b.entity(30, "BG_BOT_911", CARDTYPE="MINION", ZONE="PLAY", CONTROLLER=2,
             ATK=2, HEALTH=4, ZONE_POSITION=1)
    b.entity(31, "BG31_171te", CARDTYPE="ENCHANTMENT", ZONE="PLAY", CONTROLLER=2,
             ATTACHED=30, MAGNETIC=1, TAG_SCRIPT_DATA_NUM_1=4, TAG_SCRIPT_DATA_NUM_2=4)
    b.tag_change("GameEntity", "BOARD_VISUAL_STATE", 2)
    combats = [e for e in LiveGameProcessor().feed(b.lines)
               if isinstance(e, ev.CombatStart)]
    assert combats, "no combat snapshot"
    [minion] = [m for m in combats[-1].snapshot.opponent.minions if m.entity_id == 30]
    assert minion.linked_cards == 1
    assert minion.magnetized_stats == (4, 4)
