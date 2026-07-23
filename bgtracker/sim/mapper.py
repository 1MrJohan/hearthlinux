"""Map BoardSnapshot dataclasses to the Firestone simulator's BgsBattleInfo.

Tier 0: card ids, stats, keyword booleans, tavern tier, hero HP/armor, plus
attached enchantments (cardId + script data nums). Unmapped features (hero
powers, quests, secrets, trinkets) degrade odds accuracy, not correctness —
the calibration report in history/stats.py tells us what to map next.
"""

from __future__ import annotations

from bgtracker.state.game import BoardSnapshot, Minion, PlayerBoard


def _entity(minion: Minion, friendly: bool) -> dict:
    return {
        "entityId": minion.entity_id,
        "cardId": minion.card_id or "",
        "attack": minion.attack,
        "health": minion.health,
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
    return {
        "player": player,
        "board": [_entity(m, friendly) for m in board.minions],
    }


def to_battle_info(snapshot: BoardSnapshot) -> dict | None:
    """Simulator input for a combat snapshot; None if either board is unknown."""
    if snapshot.friendly is None or snapshot.opponent is None:
        return None
    return {
        "playerBoard": _board(snapshot.friendly, friendly=True),
        "opponentBoard": _board(snapshot.opponent, friendly=False),
        "gameState": {"currentTurn": snapshot.turn},
    }
