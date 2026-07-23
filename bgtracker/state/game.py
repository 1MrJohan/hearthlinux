"""Projection of the hslog entity tree into plain BG dataclasses.

These snapshots are the contract for everything downstream: the overlay,
the combat-sim mapper, and match history all consume them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hearthstone.entities import Card, Game
from hearthstone.enums import CardType, GameTag, Zone

from bgtracker.data import cards

_ATTACHED = GameTag.ATTACHED


def tag(entity, gametag, default=0):
    return entity.tags.get(gametag, default)


@dataclass(frozen=True)
class Enchantment:
    card_id: str | None
    num1: int = 0
    num2: int = 0


@dataclass(frozen=True)
class Minion:
    entity_id: int
    card_id: str | None
    position: int
    attack: int
    health: int
    taunt: bool = False
    divine_shield: bool = False
    poisonous: bool = False
    venomous: bool = False
    windfury: bool = False
    mega_windfury: bool = False
    reborn: bool = False
    stealth: bool = False
    golden: bool = False
    tier: int = 1
    enchantments: tuple[Enchantment, ...] = ()

    @property
    def flags(self) -> str:
        return "".join(
            flag
            for flag, on in [
                ("T", self.taunt), ("D", self.divine_shield), ("P", self.poisonous),
                ("V", self.venomous), ("W", self.windfury or self.mega_windfury),
                ("R", self.reborn), ("S", self.stealth), ("G", self.golden),
            ]
            if on
        )

    def __str__(self) -> str:
        kw = self.flags
        return f"{self.card_id or '?'} {self.attack}/{self.health}{' [' + kw + ']' if kw else ''}"


@dataclass(frozen=True)
class PlayerBoard:
    player_id: int          # log controller id (shared slot 14 for opponents!)
    bg_player_id: int       # hero PLAYER_ID tag — the real per-player identity
    hero_card_id: str | None
    hero_entity_id: int
    health: int
    armor: int
    tier: int
    minions: tuple[Minion, ...] = ()
    hero_power_id: str | None = None      # active hero power (start-of-combat)
    hero_power_used: bool = False
    # tribe/aura bonuses applied to minions summoned during combat; keys match
    # the simulator's BgsPlayerGlobalInfo (BloodGemAttackBonus, …)
    global_info: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BoardSnapshot:
    turn: int
    friendly: PlayerBoard | None
    opponent: PlayerBoard | None
    damage_cap: int = 0  # BACON_COMBAT_DAMAGE_CAP; 0 = no cap active


@dataclass
class GameSummary:
    hero_card_id: str | None = None
    placement: int | None = None
    turns: int = 0
    combats: list = field(default_factory=list)


# Player-wide "tavern buff" counters that accumulate across a game, stored as
# tags on the Player entity. Each: (label, attack-tag, health-tag). Undead has
# no equivalent player counter (tracked per-minion), so it isn't here.
_BUFF_TAGS = [
    ("Blood Gem", GameTag.BACON_BLOODGEMBUFFATKVALUE, GameTag.BACON_BLOODGEMBUFFHEALTHVALUE),
    ("Elemental", GameTag.BACON_ELEMENTAL_BUFFATKVALUE, GameTag.BACON_ELEMENTAL_BUFFHEALTHVALUE),
    ("Pirate", GameTag.BACON_PIRATE_BUFFATKVALUE, GameTag.BACON_PIRATE_BUFFHEALTHVALUE),
    ("Spell", GameTag.TAVERN_SPELL_ATTACK_INCREASE, GameTag.TAVERN_SPELL_HEALTH_INCREASE),
]


@dataclass(frozen=True)
class PlayerBuffs:
    # (label, attack, health) for each buff with a nonzero value
    entries: tuple[tuple[str, int, int], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.entries)


def read_buffs(game: Game, player_id: int) -> PlayerBuffs:
    """Read the accumulating tavern-buff counters off the friendly player."""
    player = next((p for p in game.players if p.player_id == player_id), None)
    if player is None:
        return PlayerBuffs()
    entries = []
    for label, atk_tag, hp_tag in _BUFF_TAGS:
        atk, hp = tag(player, atk_tag), tag(player, hp_tag)
        if atk or hp:
            entries.append((label, atk, hp))
    return PlayerBuffs(tuple(entries))


# Persistent tavern SPELLS the player holds (Easterly Winds and other pool
# spells). They aren't counters — they buff shop minions / the board on
# triggers — but are worth surfacing as active effects. Classified by the
# stable `isBattlegroundsPoolSpell` card flag, since the game morphs their
# runtime CARDTYPE (SPELL<->TRINKET) and that type is also polluted with
# cosmetic "Portraits" and the discover pool.
# SETASIDE/PLAY hold persistent tavern effects; HAND is excluded so a one-shot
# spell mid-cast doesn't flicker into the list.
_HELD_ZONES = (Zone.PLAY, Zone.SETASIDE)


def read_active_spells(game: Game, player_id: int) -> tuple[str, ...]:
    """Friendly-held tavern spells, deduped by card id."""
    pool = cards.pool_spell_ids()
    if not pool:
        return ()
    out: list[str] = []
    for e in game.entities:
        if (
            isinstance(e, Card)
            and e.card_id in pool
            and tag(e, GameTag.CONTROLLER) == player_id
            and tag(e, GameTag.ZONE) in _HELD_ZONES
            and e.card_id not in out
        ):
            out.append(e.card_id)
    return tuple(out)


def _minion_from(entity: Card, enchants: dict[int, list[Enchantment]]) -> Minion:
    return Minion(
        entity_id=entity.id,
        card_id=entity.card_id,
        position=tag(entity, GameTag.ZONE_POSITION),
        attack=tag(entity, GameTag.ATK),
        health=tag(entity, GameTag.HEALTH) - tag(entity, GameTag.DAMAGE),
        taunt=bool(tag(entity, GameTag.TAUNT)),
        divine_shield=bool(tag(entity, GameTag.DIVINE_SHIELD)),
        poisonous=bool(tag(entity, GameTag.POISONOUS)),
        venomous=bool(tag(entity, GameTag.VENOMOUS)),
        windfury=bool(tag(entity, GameTag.WINDFURY)),
        mega_windfury=bool(tag(entity, GameTag.MEGA_WINDFURY)),
        reborn=bool(tag(entity, GameTag.REBORN)),
        stealth=bool(tag(entity, GameTag.STEALTH)),
        golden=tag(entity, GameTag.PREMIUM) > 0,
        tier=tag(entity, GameTag.TECH_LEVEL, 1),
        enchantments=tuple(enchants.get(entity.id, ())),
    )


def _global_info(player) -> dict:
    """Tribe/aura bonuses the sim re-applies to minions summoned mid-combat.

    Keys match the simulator's BgsPlayerGlobalInfo. Read off the Player entity
    (same tags the Buffs panel uses). Only nonzero values are sent.
    """
    if player is None:
        return {}
    src = {
        "BloodGemAttackBonus": GameTag.BACON_BLOODGEMBUFFATKVALUE,
        "BloodGemHealthBonus": GameTag.BACON_BLOODGEMBUFFHEALTHVALUE,
        "ElementalAttackBuff": GameTag.BACON_ELEMENTAL_BUFFATKVALUE,
        "ElementalHealthBuff": GameTag.BACON_ELEMENTAL_BUFFHEALTHVALUE,
        "PirateAttackBuff": GameTag.BACON_PIRATE_BUFFATKVALUE,
        "PirateHealthBuff": GameTag.BACON_PIRATE_BUFFHEALTHVALUE,
        "TavernSpellAttackBuff": GameTag.TAVERN_SPELL_ATTACK_INCREASE,
        "TavernSpellHealthBuff": GameTag.TAVERN_SPELL_HEALTH_INCREASE,
    }
    return {k: tag(player, t) for k, t in src.items() if tag(player, t)}


def project_player_board(game: Game, player_id: int) -> PlayerBoard | None:
    """Project one controller's in-play hero + minions from the entity tree."""
    hero = None
    hero_power = None
    minions: list[Card] = []
    enchants: dict[int, list[Enchantment]] = {}
    for entity in game.entities:
        if not isinstance(entity, Card) or tag(entity, GameTag.ZONE) != Zone.PLAY:
            continue
        if tag(entity, GameTag.CONTROLLER) != player_id:
            continue
        ctype = entity.type
        if ctype == CardType.MINION:
            minions.append(entity)
        elif ctype == CardType.HERO:
            hero = entity
        elif ctype == CardType.HERO_POWER:
            hero_power = entity
        elif ctype == CardType.ENCHANTMENT:
            attached = tag(entity, _ATTACHED)
            if attached:
                enchants.setdefault(attached, []).append(
                    Enchantment(
                        card_id=entity.card_id,
                        num1=tag(entity, GameTag.TAG_SCRIPT_DATA_NUM_1),
                        num2=tag(entity, GameTag.TAG_SCRIPT_DATA_NUM_2),
                    )
                )
    if hero is None and not minions:
        return None
    minions.sort(key=lambda m: tag(m, GameTag.ZONE_POSITION))
    player = next((p for p in game.players if p.player_id == player_id), None)
    return PlayerBoard(
        player_id=player_id,
        bg_player_id=tag(hero, GameTag.PLAYER_ID) if hero else 0,
        hero_card_id=hero.card_id if hero else None,
        hero_entity_id=hero.id if hero else 0,
        health=(tag(hero, GameTag.HEALTH) - tag(hero, GameTag.DAMAGE)) if hero else 0,
        armor=tag(hero, GameTag.ARMOR) if hero else 0,
        tier=tag(hero, GameTag.PLAYER_TECH_LEVEL, 1) if hero else 1,
        minions=tuple(_minion_from(m, enchants) for m in minions),
        hero_power_id=hero_power.card_id if hero_power else None,
        hero_power_used=bool(tag(hero_power, GameTag.EXHAUSTED)) if hero_power else False,
        global_info=_global_info(player),
    )
