"""Build tiny synthetic Power.log content for pipeline tests.

Real captured fixtures (tests/fixtures/*.power.log) are the ground truth once
logging is enabled in-game; this keeps the pipeline testable before then.
"""

from __future__ import annotations

from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor

PREFIX = "D 00:00:{sec:02d}.0000000 GameState.DebugPrintPower() - "


class LogBuilder:
    def __init__(self):
        self.lines: list[str] = []
        self._sec = 0

    def add(self, msg: str, indent: int = 0) -> "LogBuilder":
        self._sec = min(self._sec + 1, 59)
        self.lines.append(PREFIX.format(sec=self._sec) + "    " * indent + msg)
        return self

    def entity(self, eid: int, card_id: str, **tags) -> "LogBuilder":
        self.add(f"FULL_ENTITY - Creating ID={eid} CardID={card_id}")
        for tag, value in tags.items():
            self.add(f"tag={tag} value={value}", indent=1)
        return self

    def tag_change(self, entity, tag: str, value) -> "LogBuilder":
        return self.add(f"TAG_CHANGE Entity={entity} tag={tag} value={value}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def minimal_bg_game() -> LogBuilder:
    """One-combat BG game: our 2/3 vs an enemy 1/7 taunt, we place 3rd."""
    b = LogBuilder()
    b.add("CREATE_GAME")
    b.add("GameEntity EntityID=1", indent=1)
    b.add("tag=TURN value=1", indent=2)
    b.add("Player EntityID=2 PlayerID=1 GameAccountId=[hi=1 lo=1]", indent=1)
    b.add("Player EntityID=3 PlayerID=2 GameAccountId=[hi=1 lo=2]", indent=1)
    # heroes in play
    b.entity(4, "TB_BaconShop_HERO_11", CARDTYPE="HERO", ZONE="PLAY", CONTROLLER=1,
             HEALTH=40, PLAYER_TECH_LEVEL=2)
    b.entity(5, "TB_BaconShop_HERO_22", CARDTYPE="HERO", ZONE="PLAY", CONTROLLER=2,
             HEALTH=30, PLAYER_TECH_LEVEL=1)
    # revealed card in OUR hand -> friendly-player detection says player 1
    b.entity(6, "BG_EX1_506", CARDTYPE="MINION", ZONE="HAND", CONTROLLER=1)
    b.tag_change("GameEntity", "TURN", 2)
    # boards
    b.entity(7, "BG_EX1_506", CARDTYPE="MINION", ZONE="PLAY", CONTROLLER=1,
             ATK=2, HEALTH=3, ZONE_POSITION=1)
    b.entity(8, "BG_CS2_065", CARDTYPE="MINION", ZONE="PLAY", CONTROLLER=2,
             ATK=1, HEALTH=7, TAUNT=1, ZONE_POSITION=1)
    # an equipped trinket (PLAY) and one merely offered (SETASIDE): only the
    # equipped one is real, and the zone is the only thing telling them apart
    b.entity(9, "BG30_MagicItem_988", CARDTYPE="BATTLEGROUND_TRINKET",
             ZONE="PLAY", CONTROLLER=1, TAG_SCRIPT_DATA_NUM_1=3)
    b.entity(10, "BG30_MagicItem_820", CARDTYPE="BATTLEGROUND_TRINKET",
             ZONE="SETASIDE", CONTROLLER=1)
    # combat phase round-trip
    b.tag_change("GameEntity", "BOARD_VISUAL_STATE", 2)
    b.tag_change("GameEntity", "BOARD_VISUAL_STATE", 1)
    # result
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 3)
    b.tag_change("GameEntity", "STATE", "COMPLETE")
    return b


def recruit_phase_game() -> LogBuilder:
    """A bare two-player recruit phase: heroes in play, friendly player
    established via a revealed hand card. Callers add whatever entities or
    tag changes they need to test."""
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


def feed_buffs(builder: LogBuilder) -> list:
    """Feed the log, with a trailing packet so the last one is exported.

    A packet is only exported once something follows it — the tailer has to
    assume a final line may still be half-written.
    """
    builder.tag_change("GameEntity", "TURN", 2)
    return LiveGameProcessor().feed(builder.lines)


def buffs_from(builder: LogBuilder) -> ev.Buffs | None:
    emitted = [e for e in feed_buffs(builder) if isinstance(e, ev.Buffs)]
    return emitted[-1] if emitted else None
