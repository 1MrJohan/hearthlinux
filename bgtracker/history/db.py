"""SQLite match history.

Each combat row stores the board snapshots as JSON plus the predicted odds —
this doubles as the sim-calibration corpus and as offline repro data for
mapper bugs (a mispredicted combat can be re-simulated from the row).
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bgtracker.config import DATA_DIR
from bgtracker.sim.client import SimResult
from bgtracker.state.game import (
    GHOST_HERO_PREFIX,
    BoardSnapshot,
    Enchantment,
    Minion,
    PlayerBoard,
    Secret,
    Trinket,
    is_ghost,
)

DB_FILE = DATA_DIR / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    log_id TEXT UNIQUE,      -- game-start timestamp from the log; dedupes replays
    started_at TEXT NOT NULL,
    ended_at TEXT,
    hero_card_id TEXT,
    placement INTEGER,
    final_turn INTEGER
);
CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    rating INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS combats (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id),
    turn INTEGER NOT NULL,
    opponent_hero TEXT,
    predicted_win REAL,
    predicted_tie REAL,
    predicted_loss REAL,
    outcome TEXT,            -- 'win'|'tie'|'loss'|'ghost' (damage-free)|NULL
    my_board TEXT,           -- JSON snapshot
    opp_board TEXT,
    sims_run INTEGER,        -- trials the sim actually reached, not the target
    sim_ms REAL,
    predicted_lost_lethal REAL,
    UNIQUE (game_id, turn)
);
"""

# Bumped whenever a one-shot data fixup is added below.
_USER_VERSION = 2

# Columns added to `combats` after the first release. CREATE TABLE IF NOT EXISTS
# silently leaves an existing table alone, so new columns need ALTER.
_ADDED_COMBAT_COLUMNS = (
    ("sims_run", "INTEGER"),
    ("sim_ms", "REAL"),
    ("predicted_lost_lethal", "REAL"),
    # A property of the opponent, deliberately NOT of the outcome: a ghost
    # fight that cost HP is a genuine loss and records one, but it still has to
    # leave calibration, and a flag is the only way to express both at once.
    ("opponent_is_ghost", "INTEGER"),
)

# Columns added to `ratings` for readings taken off the post-game screen. NULL
# in all three is a hand-typed reading, which is every row written before them.
_ADDED_RATING_COLUMNS = (
    ("game_id", "INTEGER"),  # the game this reading closes
    ("delta", "INTEGER"),    # the change the screen showed for that game
    ("source", "TEXT"),      # 'screen', or NULL for manual
)

# Sanity bounds for a recorded rating, not game rules: wide enough for any
# real rating, narrow enough to catch a mistyped or misread one.
RATING_MIN, RATING_MAX = 0, 30_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _board_json(board) -> str | None:
    return json.dumps(dataclasses.asdict(board)) if board is not None else None


def _minions_from(raw: list[dict]) -> tuple[Minion, ...]:
    return tuple(
        Minion(**{**m, "enchantments": tuple(Enchantment(**e) for e in m.get("enchantments", ()))})
        for m in raw
    )


def board_from_json(raw: str | None) -> PlayerBoard | None:
    """Rebuild a stored board so a recorded combat can be re-simulated offline.

    Tolerant of rows written before `hand`, `trinkets`, `secrets` and
    `global_info` existed — those predate the columns but are most of the
    calibration corpus, and a strict reader would throw the history away.
    """
    if raw is None:
        return None
    d = json.loads(raw)
    return PlayerBoard(
        **{
            **d,
            "minions": _minions_from(d.get("minions", ())),
            "hand": _minions_from(d.get("hand", ())),
            "trinkets": tuple(Trinket(**t) for t in d.get("trinkets", ())),
            "secrets": tuple(Secret(**s) for s in d.get("secrets", ())),
            "global_info": d.get("global_info", {}),
            # JSON has no tuple; a list would make the frozen board unhashable.
            "hero_power_nums": (
                tuple(d["hero_power_nums"]) if d.get("hero_power_nums") is not None else None
            ),
        }
    )


class HistoryDB:
    def __init__(self, path: Path = DB_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._open()

    def _open(self) -> None:
        """Connect, ensure the schema, and migrate. Used at open and after a reset."""
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(_SCHEMA)
        self._migrate()

    def reset(self, backup: bool = True) -> Path | None:
        """Empty the history, returning the backup's path if one was taken.

        Closes and *reopens* rather than deleting the file underneath a live
        connection: SQLite holds the descriptor, so an unlinked database keeps
        accepting writes into an inode nobody can ever read again — the tracker
        would look like it was recording and silently not be.

        What this destroys is not just a match list. It is the sim-calibration
        corpus and the stored board snapshots that let a mispredicted fight be
        re-simulated offline, so `backup` defaults to True.
        """
        # Asked before closing, and used to decide whether a backup is worth
        # writing: an empty database has nothing to preserve, and a directory
        # of empty .bak- files makes the one that matters harder to find.
        worth_keeping = self.counts() != (0, 0)
        self.conn.close()
        saved: Path | None = None
        if self.path.is_file():
            if backup and worth_keeping:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                saved = self.path.with_name(f"{self.path.name}.bak-{stamp}")
                shutil.copy2(self.path, saved)
            self.path.unlink()
        # Journal files left behind would be applied to the new database.
        for suffix in ("-wal", "-shm", "-journal"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)
        self._open()
        return saved

    def counts(self) -> tuple[int, int]:
        """(games, combats) — what a reset would be throwing away."""
        games = self.conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        combats = self.conn.execute("SELECT COUNT(*) FROM combats").fetchone()[0]
        return games, combats

    def _migrate(self) -> None:
        """Bring an older database up to the current shape."""
        for table, added in (("combats", _ADDED_COMBAT_COLUMNS),
                             ("ratings", _ADDED_RATING_COLUMNS)):
            existing = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in added:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        # Here rather than in _SCHEMA, which runs before an old table has the
        # column. One screen reading per game, so a second read is a no-op.
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ratings_game"
            " ON ratings (game_id) WHERE game_id IS NOT NULL"
        )

        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            # Retro-flag every ghost fight, including any already scored as a
            # loss: the opponent was a ghost regardless of what the fight cost.
            self.conn.execute(
                "UPDATE combats SET opponent_is_ghost = 1 WHERE opponent_hero LIKE ?",
                (GHOST_HERO_PREFIX + "%",),
            )
        if version < 1:
            # Ghost fights are damage-free, so every one of them was recorded as
            # a tie before they had their own outcome. Strictly one-shot: losing
            # to a ghost IS a real loss, and re-running this over rows written
            # after the fix would eat them. Hence the user_version gate, and the
            # 'tie'-only predicate.
            self.conn.execute(
                "UPDATE combats SET outcome = 'ghost'"
                " WHERE outcome = 'tie' AND opponent_hero LIKE ?",
                (GHOST_HERO_PREFIX + "%",),
            )
        self.conn.execute(f"PRAGMA user_version = {_USER_VERSION}")
        self.conn.commit()

    def start_game(self, log_id: str | None = None) -> int:
        if log_id:
            row = self.conn.execute(
                "SELECT id FROM games WHERE log_id = ?", (log_id,)
            ).fetchone()
            if row:  # tracker restart replaying a known game: resume its row
                return row[0]
        cur = self.conn.execute(
            "INSERT INTO games (log_id, started_at) VALUES (?, ?)", (log_id, _now())
        )
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
        # An upsert rather than INSERT OR REPLACE, because catch-up replays a
        # session from the top and re-records every combat in it. Once catch-up
        # stops simulating, that second write carries prediction=None — and
        # REPLACE would blank the very columns the calibration table is built
        # from. Boards and the ghost flag are projections of the same log and
        # overwrite freely; the prediction and the outcome are the ones a null
        # must never overwrite.
        self.conn.execute(
            "INSERT INTO combats (game_id, turn, opponent_hero, predicted_win,"
            " predicted_tie, predicted_loss, outcome, my_board, opp_board,"
            " sims_run, sim_ms, predicted_lost_lethal, opponent_is_ghost)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (game_id, turn) DO UPDATE SET"
            "  predicted_win  = COALESCE(excluded.predicted_win,  predicted_win),"
            "  predicted_tie  = COALESCE(excluded.predicted_tie,  predicted_tie),"
            "  predicted_loss = COALESCE(excluded.predicted_loss, predicted_loss),"
            "  sims_run       = COALESCE(excluded.sims_run,       sims_run),"
            "  sim_ms         = COALESCE(excluded.sim_ms,         sim_ms),"
            "  predicted_lost_lethal ="
            "    COALESCE(excluded.predicted_lost_lethal, predicted_lost_lethal),"
            "  outcome        = COALESCE(excluded.outcome,        outcome),"
            "  opponent_hero  = COALESCE(excluded.opponent_hero,  opponent_hero),"
            "  my_board          = excluded.my_board,"
            "  opp_board         = excluded.opp_board,"
            "  opponent_is_ghost = excluded.opponent_is_ghost",
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
                # How much sample the number is actually backed by — without it
                # a truncated run is indistinguishable from a full one.
                prediction.sims_run if prediction else None,
                prediction.sim_ms if prediction else None,
                prediction.lost_lethal_percent if prediction else None,
                1 if is_ghost(snapshot.opponent) else 0,
            ),
        )
        self.conn.commit()

    def end_game(self, game_id: int, placement: int | None, final_turn: int) -> None:
        # A game ends once, and the first time it was seen to end is the true
        # one. This used to be a plain assignment, so re-recording a finished
        # game on catch-up stamped ended_at with the replay's clock: 14 games
        # in the author's database claim durations over three hours, their end
        # times clustered seconds apart on the evening they were replayed.
        # Harmless only by luck — review.py groups periods on started_at, which
        # start_game preserves, and nothing reads ended_at at all.
        #
        # placement gets the same treatment for the same reason: GameEnd is
        # deferred until a placement tag appears, but finalize() can flush one
        # carrying None, which would blank a real result.
        #
        # ended_at (COALESCE keeps the first) and placement (COALESCE prefers
        # the new) point in opposite directions on purpose — the first true end
        # time is the honest one, but the first placement seen can be a None
        # finalize() flushed before the real tag landed. Do not "tidy" these to
        # match each other.
        self.conn.execute(
            "UPDATE games SET ended_at = COALESCE(ended_at, ?),"
            " placement = COALESCE(?, placement), final_turn = ?"
            " WHERE id = ?",
            (_now(), placement, final_turn, game_id),
        )
        self.conn.commit()

    def last_rating(self) -> int | None:
        row = self.conn.execute(
            "SELECT rating FROM ratings ORDER BY recorded_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def record_rating(
        self,
        rating: int,
        *,
        game_id: int | None = None,
        delta: int | None = None,
        source: str | None = None,
    ) -> bool:
        """Store a reading; False if that game already has one."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO ratings (recorded_at, rating, game_id, delta, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (_now(), rating, game_id, delta, source),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def close(self) -> None:
        self.conn.close()
