"""Map BoardSnapshot dataclasses to the Firestone simulator's BgsBattleInfo.

Tier 0: card ids, stats, keyword booleans, tavern tier, hero HP/armor, plus
attached enchantments (cardId + script data nums). Known combat state that
cannot be represented is rejected rather than silently simulated as a vanilla
stat block.
"""

from __future__ import annotations

import re

from bgtracker.data import cards
from bgtracker.state.game import BoardSnapshot, Minion, PlayerBoard, Secret


_COMBAT_TRIGGER_TEXT = re.compile(
    r"\b(?:start of combat|deathrattle|avenge|rally|"
    r"(?:whenever|when|after)\b.{0,80}\b(?:attacks|dies|died|is summoned)|"
    r"is attacked|takes damage|deals damage)\b",
    re.IGNORECASE,
)
_COMBAT_EFFECT_TEXT = re.compile(
    r"\b(?:summon|resummon|cast|deal|destroy|trigger|attack immediately|"
    r"double|swap|set|steal|transform|remove|reborn)\b|"
    r"\b(?:give|gain)\b.{0,80}(?:[+-]\d|attack|health|stats|taunt|"
    r"divine shield|windfury|reborn|venomous|stealth)",
    re.IGNORECASE,
)
_RECRUIT_ONLY_DESTINATION = re.compile(
    r"\b(?:in (?:the|bob's) tavern|tavern is|to your hand|next turn|"
    r"spellcraft|activate)\b",
    re.IGNORECASE,
)
# The Old God's hidden hero secret: counts friendly Aberration deaths and
# awakens the lobby's deity (C'Thun, Y'Shaarj) mid-combat.
_DEITY_SECRET = "BG_OldGod"


def _combat_relevant(card_id: str | None) -> bool:
    if not card_id:
        return False
    card = cards.get(card_id)
    if card is None:
        return False
    # Mechanics such as DEATHRATTLE and RALLY also cover economy-only effects
    # ("Get a card", "buff the Tavern"). Those do not alter the current fight.
    # Require a trigger and an action that can change entities in combat, in
    # the same sentence, while excluding destinations whose state is already
    # reflected by a later snapshot.
    text = re.sub(r"<[^>]+>", "", card.get("text") or "").replace("\n", " ")
    for clause in re.split(r"[.!?]", text):
        if (
            _COMBAT_TRIGGER_TEXT.search(clause)
            and _COMBAT_EFFECT_TEXT.search(clause)
            and not _RECRUIT_ONLY_DESTINATION.search(clause)
        ):
            return True
    return False


def _unmodeled_combat_power(board: PlayerBoard | None) -> str | None:
    """A visible hero power whose combat state the mapper cannot represent.

    Hero powers are deliberately omitted from the payload: Firestone needs
    per-power `info`, and a bare ID is worse than none. Most powers only shape
    the recruit board and are already reflected in its live stats. A power
    that acts during combat is different — omitting it changes this fight, so
    returning odds would present a known-incomplete simulation as fact.
    """
    if board is None or not board.hero_power_id:
        return None
    if _combat_relevant(board.hero_power_id):
        return board.hero_power_id
    return None


def _combat_card_ids(snapshot: BoardSnapshot) -> list[str]:
    """Combat-behavior IDs known by HearthstoneJSON and present in the input.

    The simulator's separately published card data can temporarily lag both
    the game and its behavior package. Sending this small capability hint lets
    the sidecar distinguish a harmless unknown stat block from a new combat
    effect whose metadata it cannot resolve.
    """
    result = set()
    for board in (snapshot.friendly, snapshot.opponent):
        if board is None:
            continue
        for minion in (*board.minions, *board.hand):
            if _combat_relevant(minion.card_id):
                result.add(minion.card_id)
            for enchantment in minion.enchantments:
                if _combat_relevant(enchantment.card_id):
                    result.add(enchantment.card_id)
        for trinket in board.trinkets:
            if _combat_relevant(trinket.card_id):
                result.add(trinket.card_id)
        # A secret is combat behavior by definition. Mark it even when its
        # wording falls outside the text heuristic so the exact Firestone
        # package/data pair has to prove it can model the entity.
        result.update(secret.card_id for secret in board.secrets if secret.card_id)
    return sorted(result)


def simulation_blocker(snapshot: BoardSnapshot) -> str | None:
    """Why this snapshot cannot produce an honest forecast, if anything."""
    if snapshot.friendly is None or snapshot.opponent is None:
        return "board incomplete"
    powers = tuple(
        power
        for power in (
            _unmodeled_combat_power(snapshot.friendly),
            _unmodeled_combat_power(snapshot.opponent),
        )
        if power is not None
    )
    if powers:
        return f"combat hero power not modeled: {', '.join(powers)}"
    if any(
        not secret.card_id
        for board in (snapshot.friendly, snapshot.opponent)
        for secret in board.secrets
    ):
        return "combat secret identity hidden"
    # Without NUM_6 Firestone awakens C'Thun whatever the lobby's deity is,
    # and without NUM_3 at 1 health. Live snapshots always carry both; combat
    # rows stored before they were read do not.
    if any(
        secret.card_id == _DEITY_SECRET and not secret.num6
        for board in (snapshot.friendly, snapshot.opponent)
        for secret in board.secrets
    ):
        return "deity secret incomplete"
    return None


def _secret(secret: Secret) -> dict:
    result = {
        "cardId": secret.card_id,
        "entityId": secret.entity_id,
        "scriptDataNum1": secret.num1,
        "scriptDataNum2": secret.num2,
    }
    # Deity only. Other secrets (BG28_603) set NUM_3/NUM_6 too, but have always
    # reached the sim without them, and nothing shows what it would do with
    # them — an explicit value changes how its `??` fallbacks resolve.
    if secret.card_id == _DEITY_SECRET:
        result["scriptDataNum3"] = secret.num3
        result["scriptDataNum6"] = secret.num6
    return result


def _entity(minion: Minion, friendly: bool) -> dict:
    return {
        "entityId": minion.entity_id,
        "cardId": minion.card_id or "",
        "attack": minion.attack,
        "health": minion.health,
        # Read directly by effects (Captain Sanders' `e.tavernTier <= 6`,
        # avenge summons scaling) with no card-DB fallback in the simulator —
        # absent, those comparisons are silently false, not approximate.
        "tavernTier": minion.tier,
        "taunt": minion.taunt,
        "divineShield": minion.divine_shield,
        "poisonous": minion.poisonous,
        "venomous": minion.venomous,
        "windfury": minion.windfury or minion.mega_windfury,
        "reborn": minion.reborn,
        "stealth": minion.stealth,
        "friendly": friendly,
        "enchantments": [
            {
                "cardId": e.card_id or "",
                "tagScriptDataNum1": e.num1,
                "tagScriptDataNum2": e.num2,
                "timing": i,
            }
            for i, e in enumerate(minion.enchantments)
        ],
    }


def _board(board: PlayerBoard, friendly: bool) -> dict:
    # NOTE: hero powers are deliberately NOT sent. The sim needs per-power
    # `info` state to apply start-of-combat effects correctly; sending just an
    # id (info=0) makes it misapply even non-combat powers (verified: swung a
    # 16% combat to 0%). globalInfo is safe — it only buffs minions summoned
    # during combat to match the current tribe-aura level.
    player = {
        "cardId": board.hero_card_id or "",
        "entityId": board.hero_entity_id,
        "hpLeft": board.health + board.armor,
        "tavernTier": board.tier,
        "heroPowers": [],
        "questEntities": [],
        "friendly": friendly,
    }
    if board.global_info:
        player["globalInfo"] = board.global_info
    # Start-of-combat cards act from hand (Flighty Scout summons itself,
    # Diremuck Forager pulls Murlocs out), so a hand-based build reads as a
    # near-certain loss without this. Only ever set for the friendly board —
    # the opponent's hand is hidden, so it projects empty.
    if board.hand:
        player["hand"] = [_entity(m, friendly) for m in board.hand]
    # Equipped trinkets. Most are economy effects the simulator ignores, but
    # some act at the start of combat. Visible for both players.
    if board.trinkets:
        player["trinkets"] = [
            {
                "cardId": t.card_id,
                "entityId": t.entity_id,
                "scriptDataNum1": t.num1,
                "scriptDataNum2": t.num2,
            }
            for t in board.trinkets
        ]
    if board.secrets:
        player["secrets"] = [_secret(secret) for secret in board.secrets]
    return {
        "player": player,
        "board": [_entity(m, friendly) for m in board.minions],
    }


def to_battle_info(snapshot: BoardSnapshot) -> dict | None:
    """Simulator input for a combat snapshot; None if either board is unknown."""
    if simulation_blocker(snapshot) is not None:
        return None
    info = {
        "playerBoard": _board(snapshot.friendly, friendly=True),
        "opponentBoard": _board(snapshot.opponent, friendly=False),
        "gameState": {"currentTurn": snapshot.turn},
    }
    combat_card_ids = _combat_card_ids(snapshot)
    if combat_card_ids:
        info["trackerCombatCardIds"] = combat_card_ids
    return info
