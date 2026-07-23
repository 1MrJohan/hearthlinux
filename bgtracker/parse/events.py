"""Typed events emitted by the live parser onto the app-wide queue."""

from __future__ import annotations

from dataclasses import dataclass

from bgtracker.state.game import BoardSnapshot


@dataclass(frozen=True)
class GameStart:
    game_type: int | None = None
    # Log-derived identity (game start timestamp) — stable across tracker
    # restarts that replay the same log, used to dedupe history rows.
    log_id: str | None = None


@dataclass(frozen=True)
class HeroPicked:
    card_id: str


@dataclass(frozen=True)
class TurnChange:
    turn: int


@dataclass(frozen=True)
class CombatStart:
    snapshot: BoardSnapshot


@dataclass(frozen=True)
class CombatEnd:
    snapshot: BoardSnapshot


@dataclass(frozen=True)
class ShopReady:
    """The combat animation finished and the player is back at the shop.

    CombatEnd fires when the engine has *resolved* the fight, which happens
    about a second after it starts; the client then spends 20-45s animating
    it. Anything the player looks at — the phase title, the odds — must follow
    this event instead, or it changes while they are still watching the fight.
    """


@dataclass(frozen=True)
class NextOpponent:
    player_id: int


@dataclass(frozen=True)
class Standing:
    """One player's row on the leaderboard."""

    place: int
    player_id: int          # hero PLAYER_ID tag — the stable per-player identity
    hero_card_id: str | None
    health: int = 0
    armor: int = 0
    dead: bool = False
    you: bool = False

    @property
    def total_health(self) -> int:
        return self.health + self.armor


@dataclass(frozen=True)
class Standings:
    """Current leaderboard order, best place first."""

    places: tuple[Standing, ...]


@dataclass(frozen=True)
class GameEnd:
    placement: int | None


@dataclass(frozen=True)
class Buffs:
    """Friendly player's tavern buffs and held tavern spells/trinkets.

    entries: (label, atk, health) accumulating counters.
    spells:  card ids of persistent tavern spells/trinkets held.
    """

    entries: tuple[tuple[str, int, int], ...]
    spells: tuple[str, ...] = ()


Event = (
    GameStart
    | HeroPicked
    | TurnChange
    | CombatStart
    | CombatEnd
    | ShopReady
    | NextOpponent
    | Standings
    | GameEnd
    | Buffs
)
