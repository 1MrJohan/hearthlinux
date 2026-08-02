"""Projection of the hslog entity tree into plain BG dataclasses.

These snapshots are the contract for everything downstream: the overlay,
the combat-sim mapper, and match history all consume them.
"""

from __future__ import annotations

import re
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
class Trinket:
    """An equipped Battlegrounds trinket.

    Most trinkets are economy effects the simulator ignores, but some carry
    start-of-combat behaviour. Both players' equipped trinkets are visible, so
    unlike the hand this maps for the opponent too.
    """

    card_id: str
    entity_id: int
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
    # Minions held in hand. Several Battlegrounds cards act from hand at the
    # start of combat — Flighty Scout summons a copy of itself, Diremuck
    # Forager pulls Murlocs out, Choral Mrrrglr eats the hand's stats — so a
    # forecast that ignores the hand can be wildly wrong for those builds.
    # Only the friendly hand is ever populated; the opponent's is hidden.
    hand: tuple[Minion, ...] = ()
    # Equipped trinkets only — offers and rejected discoveries sit in
    # SETASIDE/REMOVEDFROMGAME, so the zone is what separates them.
    trinkets: tuple[Trinket, ...] = ()
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
# dedicated tags on the Player entity. Each: (label, attack-tag, health-tag).
# Curated to what the Buffs panel shows — Pirate and the played-Elemental
# counter used to be here too; see
# docs/superpowers/specs/2026-07-30-buffs-panel-curation-design.md.
_BUFF_TAGS = [
    ("Blood Gem", GameTag.BACON_BLOODGEMBUFFATKVALUE, GameTag.BACON_BLOODGEMBUFFHEALTHVALUE),
    ("Spell Power", GameTag.TAVERN_SPELL_ATTACK_INCREASE, GameTag.TAVERN_SPELL_HEALTH_INCREASE),
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


# Buffs that apply to minions *while they sit in Bob's tavern* — Nomi and the
# like. Unlike the counters above these are not player tags: the game keeps one
# enchantment per tribe attached to the player entity, and every source feeding
# that tribe stacks into the same one (Dune Dweller + Nomi + a Nomi Sticker
# magic item all read as a single +27/+27). So this is a read of a running
# total, never a count of triggers.
#
# Two things here are silent when wrong:
#   * Match the id exactly. The sibling `BG_ShopBuff*_Ench` ids are the
#     per-minion "Tavern Buffed" marks on the shop minions themselves; a prefix
#     match would sum every buffed minion in the tavern into the player total.
#   * The value is in TAG_SCRIPT_DATA_NUM_1/2. These entities carry no ATK or
#     HEALTH, so reading those yields a silent 0/0 rather than an error.
#
# `MultiRace` covers several tribes at once but the log never says which, so it
# is labelled for what it is rather than guessed at.
_SHOP_BUFFS = {
    "BG_ShopBuff": "All minions",
    "BG_ShopBuff_MultiRace": "Multi-tribe",
    "BG_ShopBuff_Beast": "Beast",
    "BG_ShopBuff_Demon": "Demon",
    "BG_ShopBuff_Dragon": "Dragon",
    "BG_ShopBuff_Elemental": "Elemental",
    "BG_ShopBuff_Mech": "Mech",
    "BG_ShopBuff_Murloc": "Murloc",
    "BG_ShopBuff_Naga": "Naga",
    "BG_ShopBuff_Pirate": "Pirate",
    "BG_ShopBuff_Quilboar": "Quilboar",
    "BG_ShopBuff_Undead": "Undead",
}


def _read_script_buffs(
    game: Game, player_id: int, mapping: dict[str, str]
) -> tuple[tuple[str, int, int], ...]:
    """Read (label, attack, health) off player-owned script-data enchantments.

    Shared shape behind both the tavern shop buffs and the played board-wide
    buffs (Undead, Beetle Army): one enchantment per source, attached to the
    player, holding a running total in TAG_SCRIPT_DATA_NUM_1/2. `mapping` is
    the only thing that differs between callers.
    """
    found: dict[str, tuple[int, int]] = {}
    for e in game.entities:
        if (
            isinstance(e, Card)
            and e.card_id in mapping
            and tag(e, GameTag.CONTROLLER) == player_id
            and tag(e, GameTag.ZONE) == Zone.PLAY
        ):
            atk = tag(e, GameTag.TAG_SCRIPT_DATA_NUM_1)
            hp = tag(e, GameTag.TAG_SCRIPT_DATA_NUM_2)
            if atk or hp:
                found[e.card_id] = (atk, hp)
    return tuple(
        (label, *found[card_id])
        for card_id, label in mapping.items()
        if card_id in found
    )


def read_shop_buffs(game: Game, player_id: int) -> tuple[tuple[str, int, int], ...]:
    """Friendly tavern-wide buffs, as (label, attack, health).

    Declared order, not discovery order, so a row keeps its place in the panel
    as its value climbs.
    """
    return _read_script_buffs(game, player_id, _SHOP_BUFFS)


# Buffs that accumulate on the board itself, in the same per-player
# TAG_SCRIPT_DATA_NUM_1/2 enchantment shape read_shop_buffs already reads — a
# different quantity from the tavern buffs above (already baked into your
# minions, not a purchasing decision) but mechanically the same read.
#
# Undead only ever sets NUM_1: the card text is "Give Attack to Undead", so
# health stays 0 rather than absent. Beetle Army sets both.
_PLAYED_SCRIPT_BUFFS = {
    "BG25_011pe": "Undead",
    "BG31_808pe": "Beetle Army",
}


def read_played_buffs(game: Game, player_id: int) -> tuple[tuple[str, int, int], ...]:
    """Friendly played board-wide buffs not covered by a dedicated GameTag."""
    return _read_script_buffs(game, player_id, _PLAYED_SCRIPT_BUFFS)


def read_gold_next_turn(game: Game, player_id: int) -> int:
    """Net gold banked for next turn; negative if overdrawn."""
    player = next((p for p in game.players if p.player_id == player_id), None)
    if player is None:
        return 0
    return (tag(player, GameTag.BACON_PLAYER_EXTRA_GOLD_NEXT_TURN)
            - tag(player, GameTag.BACON_PLAYER_OVERDRAWN_GOLD_NEXT_TURN))


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


# When an odd number of players remain, somebody is paired against a "ghost" —
# a copy of an eliminated player's board, fronted by Kel'Thuzad.
#
# The ghost's hero is a dead player's entity, so its HP is not a readable
# signal: every recorded ghost fight shows it at 0 or negative. A *win* against
# one therefore cannot be told from a tie, and app.py gives that case its own
# outcome. A ghost fight that costs you HP is still a genuine loss and records
# one — do not assume these fights are damage-free. Either way the opponent was
# a ghost, which is why history/db.py flags the row and stats.py drops the whole
# category: scoring only the fights that happen to be legible would bias the
# calibration table toward losses.
#
# Prefix match, because heroes carry `_SKIN_*` variants.
GHOST_HERO_PREFIX = "TB_BaconShop_HERO_KelThuzad"


def is_ghost(board: "PlayerBoard | None") -> bool:
    """Whether this board is the odd-player-out ghost rather than a real player."""
    return bool(board and board.hero_card_id and board.hero_card_id.startswith(GHOST_HERO_PREFIX))


# Not equipment: these sit in play announcing "the Trinket Shop opens in N
# turns". They carry the trinket card type, so only the id tells them apart.
# The number is per-set (BG30_Trinket_1st, BG34_Trinket_1st, …), so match on the
# shape rather than a frozen set of ids that silently rots when the set rotates.
# Anchored: it must not swallow the BG##_Trinket_1st_Spell "Buy a … Trinket" pool
# spells, which are already excluded by the pool-spell check above.
_TRINKET_PLACEHOLDER_RE = re.compile(r"^BG\d+_Trinket_(?:1st|2nd)$")


def _is_trinket(entity: Card) -> bool:
    """Whether an in-play card is an equipped trinket.

    Checked two ways so neither failure mode is silent: the runtime CARDTYPE
    works even with the card DB unavailable (`--no-names`), and the DB catches
    anything the game has morphed away from it. Pool spells are excluded
    explicitly — the game morphs *those* into TRINKET at runtime, which is the
    one case where the type alone would lie.
    """
    if entity.card_id in cards.pool_spell_ids():
        return False
    if entity.card_id and _TRINKET_PLACEHOLDER_RE.match(entity.card_id):
        return False
    return (
        entity.type == CardType.BATTLEGROUND_TRINKET
        or entity.card_id in cards.trinket_ids()
    )


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
    hand: list[Card] = []
    trinkets: list[Card] = []
    enchants: dict[int, list[Enchantment]] = {}
    for entity in game.entities:
        if not isinstance(entity, Card):
            continue
        if tag(entity, GameTag.CONTROLLER) != player_id:
            continue
        zone = tag(entity, GameTag.ZONE)
        if zone == Zone.HAND:
            # A card with no id is an opponent's hidden card; nothing to send.
            if entity.type == CardType.MINION and entity.card_id:
                hand.append(entity)
            continue
        if zone != Zone.PLAY:
            continue
        if _is_trinket(entity):
            trinkets.append(entity)
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
        hand=tuple(_minion_from(m, enchants) for m in hand),
        trinkets=tuple(
            Trinket(
                card_id=t.card_id,
                entity_id=t.id,
                num1=tag(t, GameTag.TAG_SCRIPT_DATA_NUM_1),
                num2=tag(t, GameTag.TAG_SCRIPT_DATA_NUM_2),
            )
            for t in trinkets
        ),
        hero_power_id=hero_power.card_id if hero_power else None,
        hero_power_used=bool(tag(hero_power, GameTag.EXHAUSTED)) if hero_power else False,
        global_info=_global_info(player),
    )
