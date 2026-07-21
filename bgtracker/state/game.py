"""Projection of the hslog entity tree into plain BG dataclasses.

These snapshots are the contract for everything downstream: the overlay,
the combat-sim mapper, and match history all consume them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hearthstone.entities import Card, Game
from hearthstone.enums import CardType, GameTag, Zone

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

    def __str__(self) -> str:
        kw = "".join(
            flag
            for flag, on in [
                ("T", self.taunt), ("D", self.divine_shield), ("P", self.poisonous),
                ("V", self.venomous), ("W", self.windfury or self.mega_windfury),
                ("R", self.reborn), ("S", self.stealth), ("G", self.golden),
            ]
            if on
        )
        return f"{self.card_id or '?'} {self.attack}/{self.health}{' [' + kw + ']' if kw else ''}"


@dataclass(frozen=True)
class PlayerBoard:
    player_id: int
    hero_card_id: str | None
    hero_entity_id: int
    health: int
    armor: int
    tier: int
    minions: tuple[Minion, ...] = ()


@dataclass(frozen=True)
class BoardSnapshot:
    turn: int
    friendly: PlayerBoard | None
    opponent: PlayerBoard | None


@dataclass
class GameSummary:
    hero_card_id: str | None = None
    placement: int | None = None
    turns: int = 0
    combats: list = field(default_factory=list)


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


def project_player_board(game: Game, player_id: int) -> PlayerBoard | None:
    """Project one controller's in-play hero + minions from the entity tree."""
    hero = None
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
    return PlayerBoard(
        player_id=player_id,
        hero_card_id=hero.card_id if hero else None,
        hero_entity_id=hero.id if hero else 0,
        health=(tag(hero, GameTag.HEALTH) - tag(hero, GameTag.DAMAGE)) if hero else 0,
        armor=tag(hero, GameTag.ARMOR) if hero else 0,
        tier=tag(hero, GameTag.PLAYER_TECH_LEVEL, 1) if hero else 1,
        minions=tuple(_minion_from(m, enchants) for m in minions),
    )
