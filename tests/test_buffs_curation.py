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
