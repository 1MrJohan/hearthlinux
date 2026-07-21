"""SQLite match history.

Each combat row stores the board snapshots as JSON plus the predicted odds —
this doubles as the sim-calibration corpus and as offline repro data for
mapper bugs (a mispredicted combat can be re-simulated from the row).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bgtracker.config import DATA_DIR
from bgtracker.sim.client import SimResult
from bgtracker.state.game import BoardSnapshot

DB_FILE = DATA_DIR / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    hero_card_id TEXT,
    placement INTEGER,
    final_turn INTEGER
);
CREATE TABLE IF NOT EXISTS combats (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id),
    turn INTEGER NOT NULL,
    opponent_hero TEXT,
    predicted_win REAL,
    predicted_tie REAL,
    predicted_loss REAL,
    outcome TEXT,            -- 'win' | 'tie' | 'loss' | NULL (unknown)
    my_board TEXT,           -- JSON snapshot
    opp_board TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _board_json(board) -> str | None:
    return json.dumps(dataclasses.asdict(board)) if board is not None else None


class HistoryDB:
    def __init__(self, path: Path = DB_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(_SCHEMA)

    def start_game(self) -> int:
        cur = self.conn.execute("INSERT INTO games (started_at) VALUES (?)", (_now(),))
        self.conn.commit()
        return cur.lastrowid

    def set_hero(self, game_id: int, hero_card_id: str) -> None:
        self.conn.execute("UPDATE games SET hero_card_id=? WHERE id=?", (hero_card_id, game_id))
        self.conn.commit()

    def record_combat(
        self,
        game_id: int,
        snapshot: BoardSnapshot,
        prediction: SimResult | None,
        outcome: str | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO combats (game_id, turn, opponent_hero, predicted_win,"
            " predicted_tie, predicted_loss, outcome, my_board, opp_board)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                game_id,
                snapshot.turn,
                snapshot.opponent.hero_card_id if snapshot.opponent else None,
                prediction.won_percent if prediction else None,
                prediction.tied_percent if prediction else None,
                prediction.lost_percent if prediction else None,
                outcome,
                _board_json(snapshot.friendly),
                _board_json(snapshot.opponent),
            ),
        )
        self.conn.commit()

    def end_game(self, game_id: int, placement: int | None, final_turn: int) -> None:
        self.conn.execute(
            "UPDATE games SET ended_at=?, placement=?, final_turn=? WHERE id=?",
            (_now(), placement, final_turn, game_id),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
