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
class NextOpponent:
    player_id: int


@dataclass(frozen=True)
class Standings:
    """Current leaderboard order: (place, player_id, hero_card_id) tuples."""

    places: tuple[tuple[int, int, str | None], ...]


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
    | NextOpponent
    | Standings
    | GameEnd
    | Buffs
)
