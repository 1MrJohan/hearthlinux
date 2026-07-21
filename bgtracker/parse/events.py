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
class GameEnd:
    placement: int | None


Event = GameStart | HeroPicked | TurnChange | CombatStart | CombatEnd | NextOpponent | GameEnd
