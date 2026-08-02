"""Buffs panel curation: a fixed set of played-buff counters and turn
economy, replacing the old show-everything-nonzero panel.

See docs/superpowers/specs/2026-07-30-buffs-panel-curation-design.md for the
log evidence behind every reader in this module.
"""

from .synthetic import buffs_from, recruit_phase_game


def test_blood_gem_and_spell_power_are_read_from_the_player():
    b = recruit_phase_game()
    b.tag_change(2, "BACON_BLOODGEMBUFFATKVALUE", 2)
    b.tag_change(2, "BACON_BLOODGEMBUFFHEALTHVALUE", 2)
    b.tag_change(2, "TAVERN_SPELL_ATTACK_INCREASE", 1)
    b.tag_change(2, "TAVERN_SPELL_HEALTH_INCREASE", 1)
    assert buffs_from(b).entries == (("Blood Gem", 2, 2), ("Spell Power", 1, 1))


def test_pirate_and_played_elemental_no_longer_produce_entries():
    """Both were rows before this feature curated the panel down; neither
    tag is in _BUFF_TAGS anymore."""
    b = recruit_phase_game()
    b.tag_change(2, "BACON_PIRATE_BUFFATKVALUE", 3)
    b.tag_change(2, "BACON_PIRATE_BUFFHEALTHVALUE", 3)
    b.tag_change(2, "BACON_ELEMENTAL_BUFFATKVALUE", 2)
    b.tag_change(2, "BACON_ELEMENTAL_BUFFHEALTHVALUE", 2)
    buffs = buffs_from(b)
    assert buffs is None or buffs.entries == ()


def test_undead_bonus_attack_is_read_as_a_played_buff():
    b = recruit_phase_game()
    b.entity(20, "BG25_011pe", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=4)
    assert buffs_from(b).entries == (("Undead", 4, 0),)


def test_beetle_army_is_read_as_a_played_buff():
    b = recruit_phase_game()
    b.entity(20, "BG31_808pe", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2, TAG_SCRIPT_DATA_NUM_1=12,
             TAG_SCRIPT_DATA_NUM_2=8)
    assert buffs_from(b).entries == (("Beetle Army", 12, 8),)


def test_another_controllers_played_script_buff_is_not_ours():
    b = recruit_phase_game()
    b.entity(20, "BG25_011pe", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=2, ATTACHED=3, TAG_SCRIPT_DATA_NUM_1=4)
    buffs = buffs_from(b)
    assert buffs is None or buffs.entries == ()


def test_a_zero_valued_played_script_buff_makes_no_entry():
    """The enchantment is created before the first trigger sets its value."""
    b = recruit_phase_game()
    b.entity(20, "BG31_808pe", CARDTYPE="ENCHANTMENT", ZONE="PLAY",
             CONTROLLER=1, ATTACHED=2)
    buffs = buffs_from(b)
    assert buffs is None or buffs.entries == ()


def test_gold_banked_for_next_turn_is_read():
    b = recruit_phase_game()
    b.tag_change(2, "BACON_PLAYER_EXTRA_GOLD_NEXT_TURN", 2)
    assert buffs_from(b).gold_next_turn == 2


def test_overdrawn_gold_next_turn_reads_negative():
    b = recruit_phase_game()
    b.tag_change(2, "BACON_PLAYER_OVERDRAWN_GOLD_NEXT_TURN", 1)
    assert buffs_from(b).gold_next_turn == -1


def test_extra_and_overdrawn_gold_next_turn_net_together():
    b = recruit_phase_game()
    b.tag_change(2, "BACON_PLAYER_EXTRA_GOLD_NEXT_TURN", 3)
    b.tag_change(2, "BACON_PLAYER_OVERDRAWN_GOLD_NEXT_TURN", 1)
    assert buffs_from(b).gold_next_turn == 2


def test_no_gold_next_turn_tag_reads_zero():
    b = recruit_phase_game()
    buffs = buffs_from(b)
    assert buffs is None or buffs.gold_next_turn == 0


def test_free_rerolls_available_this_turn_are_read():
    b = recruit_phase_game()
    b.entity(20, "Bacon_Free_Refresh_Player_Ench", CARDTYPE="ENCHANTMENT",
             ZONE="PLAY", CONTROLLER=1, ATTACHED=2, BACON_FREE_REFRESH_COUNT=1)
    assert buffs_from(b).free_rerolls == 1


def test_another_controllers_free_reroll_enchantment_is_not_ours():
    b = recruit_phase_game()
    b.entity(20, "Bacon_Free_Refresh_Player_Ench", CARDTYPE="ENCHANTMENT",
             ZONE="PLAY", CONTROLLER=2, ATTACHED=3, BACON_FREE_REFRESH_COUNT=1)
    buffs = buffs_from(b)
    assert buffs is None or buffs.free_rerolls == 0


def test_no_free_reroll_enchantment_reads_zero():
    b = recruit_phase_game()
    buffs = buffs_from(b)
    assert buffs is None or buffs.free_rerolls == 0
