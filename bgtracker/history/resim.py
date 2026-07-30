"""Re-run recorded combats through the current mapper and simulator.

Every combat row stores both board snapshots as JSON alongside the prediction
that was made at the time, which makes the history a regression corpus: change
the mapper, the sidecar, or the sim options, and this replays hundreds of real
boards to show what moved. Rows are printed worst-disagreement first, because
that is where a mapping bug shows up.

Ghost fights are skipped — their outcome is unknowable by design, so they can
neither confirm nor contradict a prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bgtracker.data import cards
from bgtracker.history.db import DB_FILE, HistoryDB, board_from_json
from bgtracker.sim.client import SimClient
from bgtracker.sim.mapper import to_battle_info
from bgtracker.state.game import BoardSnapshot


@dataclass
class Row:
    turn: int
    opponent_hero: str | None
    outcome: str | None
    then: float | None          # win% predicted when the combat was played
    now: float | None           # win% the current code predicts
    sims_run: int = 0
    sim_ms: float = 0.0

    @property
    def drift(self) -> float:
        """How far the current code has moved from the recorded prediction."""
        if self.then is None or self.now is None:
            return 0.0
        return abs(self.now - self.then)


def _rows(conn, limit: int | None) -> list[tuple]:
    sql = (
        "SELECT turn, opponent_hero, outcome, predicted_win, my_board, opp_board"
        " FROM combats"
        " WHERE my_board IS NOT NULL AND opp_board IS NOT NULL"
        # The flag, not the outcome: a ghost fight that cost HP is recorded as a
        # genuine 'loss', and it still cannot score a prediction. Same predicate
        # stats.py uses, for the same reason.
        " AND COALESCE(opponent_is_ghost, 0) = 0"
        " ORDER BY id DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


async def resim(path: Path = DB_FILE, limit: int | None = None) -> str:
    if not path.is_file():
        return "no match history yet"
    # Opened through HistoryDB, never a bare sqlite3.connect: the migrations
    # live there, and the ghost flag this filters on is one of them. A reader
    # that skipped them would silently resim ghost fights on any database
    # written before the flag existed.
    db = HistoryDB(path)
    stored = _rows(db.conn, limit)
    db.close()
    if not stored:
        return "no recorded combats with stored boards"

    sim = SimClient()
    if not await sim.ping():
        await sim.close()
        return "combat simulator unavailable (node/sidecar missing?)"

    results: list[Row] = []
    try:
        for turn, opp_hero, outcome, then, my_json, opp_json in stored:
            snapshot = BoardSnapshot(
                turn=turn,
                friendly=board_from_json(my_json),
                opponent=board_from_json(opp_json),
            )
            info = to_battle_info(snapshot)
            row = Row(turn=turn, opponent_hero=opp_hero, outcome=outcome, then=then, now=None)
            if info is not None:
                try:
                    current = await sim.simulate(info)
                    row.now = current.won_percent
                    row.sims_run = current.sims_run
                    row.sim_ms = current.sim_ms
                except Exception as exc:  # a board the current mapper chokes on is a finding
                    row.now = None
                    print(f"  turn {turn}: simulation failed: {exc!r}")
            results.append(row)
    finally:
        await sim.close()

    return _report(results)


def _report(results: list[Row]) -> str:
    scored = [r for r in results if r.then is not None and r.now is not None]
    lines = [f"re-simulated {len(results)} combats ({len(scored)} comparable)"]

    timed = [r.sim_ms for r in results if r.sim_ms > 0]
    if timed:
        # This is the throughput bench: how long a real board costs under
        # whatever CPU policy and worker count the sidecar just ran with.
        ordered = sorted(timed)
        lines.append(
            f"  wall time: mean {sum(timed) / len(timed):.0f}ms, "
            f"median {ordered[len(ordered) // 2]:.0f}ms, max {ordered[-1]:.0f}ms"
        )

    if scored:
        moved = [r for r in scored if r.drift >= 1.0]
        mean_drift = sum(r.drift for r in scored) / len(scored)
        lines.append(f"  mean drift {mean_drift:.2f} points; {len(moved)} moved by >=1 point")
        thin = [r for r in scored if 0 < r.sims_run < 4000]
        if thin:
            # A truncated run is a noisy number, not a wrong one — worth knowing
            # before reading any drift as a mapping change.
            lines.append(f"  {len(thin)} run(s) came back with under 4000 trials")

    lines.append("")
    lines.append(f"  {'turn':>4} {'then':>7} {'now':>7} {'drift':>7} {'actual':>7}  opponent")
    for r in sorted(scored, key=lambda r: -r.drift)[:25]:
        lines.append(
            f"  {r.turn:>4} {r.then:>6.1f}% {r.now:>6.1f}% {r.drift:>6.1f}p"
            f" {str(r.outcome):>7}  {cards.name(r.opponent_hero)}"
        )
    if not scored:
        lines.append("  (nothing comparable — no stored predictions)")
    return "\n".join(lines)
