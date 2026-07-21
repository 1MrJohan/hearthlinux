"""Remember each opponent's last-seen board (the Firestone 'opponent memory')."""

from __future__ import annotations

from dataclasses import dataclass, field

from bgtracker.state.game import PlayerBoard


@dataclass
class SeenBoard:
    turn: int
    board: PlayerBoard


@dataclass
class OpponentMemory:
    _seen: dict[int, SeenBoard] = field(default_factory=dict)

    def record(self, turn: int, board: PlayerBoard | None) -> None:
        if board is not None:
            self._seen[board.player_id] = SeenBoard(turn=turn, board=board)

    def last_seen(self, player_id: int) -> SeenBoard | None:
        return self._seen.get(player_id)

    def reset(self) -> None:
        self._seen.clear()
