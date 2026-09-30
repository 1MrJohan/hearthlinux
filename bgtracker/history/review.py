"""Read-side queries for the history window: periods, games, combats, MMR.

Pure data — no GTK imports — so the window stays a thin renderer and the CLI
could grow the same views later. Opened through `HistoryDB` (never a bare
connection) so schema migrations always apply before anything reads.

Timestamps are stored in UTC; grouping into days/weeks/months happens in the
*caller's* timezone (defaulting to the machine's), because "how did I do on
Tuesday" is a local-calendar question. MMR readings are snapshots, not
per-game facts, so every derived number here says only what the readings can
honestly support: a period's net is the difference between the last readings
before its end and before its start, and a single game gets a delta only when
the post-game screen showed one for it, or when it is the *only* game between
two consecutive readings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from bgtracker.history.db import HistoryDB

BUCKETS = ("day", "week", "month")


@dataclass(frozen=True)
class PeriodRow:
    period: str            # '2026-07-21' | '2026-W30' | '2026-07'
    games: int             # all games started in the period, finished or not
    placed: int            # finished games — the denominator for any rate
    avg_placement: float | None   # over finished games only
    top4: int
    firsts: int
    net_mmr: int | None


@dataclass(frozen=True)
class GameRow:
    id: int
    started_at: datetime   # aware, in the grouping timezone
    hero_card_id: str | None
    placement: int | None  # None: unfinished (or tracker died mid-game)
    final_turn: int | None
    mmr_delta: int | None  # the screen's, or when readings isolate this one game


@dataclass(frozen=True)
class CombatRow:
    turn: int
    opponent_hero: str | None
    predicted_win: float | None
    predicted_tie: float | None
    predicted_loss: float | None
    outcome: str | None    # 'win' | 'tie' | 'loss' | 'ghost' | None
    is_ghost: bool


def _local(stamp: str, tz: tzinfo | None) -> datetime:
    return datetime.fromisoformat(stamp).astimezone(tz)


def mmr_series(db: HistoryDB, tz: tzinfo | None = None) -> list[tuple[datetime, int]]:
    """Every recorded rating, oldest first."""
    rows = db.conn.execute(
        "SELECT recorded_at, rating FROM ratings ORDER BY recorded_at"
    ).fetchall()
    return [(_local(at, tz), rating) for at, rating in rows]


def mmr_breaks(db: HistoryDB, tz: tzinfo | None = None) -> list[datetime]:
    """Readings where the chain of screen deltas does not join up.

    A screen reading also says what the rating was *before* its game
    (rating − delta). When that differs from the previous reading, games were
    played the tracker never saw, or a reading is missing, and a line drawn
    straight between the two would claim a history nobody recorded.
    """
    rows = db.conn.execute(
        "SELECT recorded_at, rating, delta FROM ratings ORDER BY recorded_at"
    ).fetchall()
    breaks = []
    for (_, previous, _), (at, rating, delta) in zip(rows, rows[1:]):
        if delta is not None and rating - delta != previous:
            breaks.append(_local(at, tz))
    return breaks


def _period_key(moment: datetime, bucket: str) -> str:
    if bucket == "day":
        return moment.strftime("%Y-%m-%d")
    if bucket == "week":
        iso = moment.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if bucket == "month":
        return moment.strftime("%Y-%m")
    raise ValueError(f"unknown bucket {bucket!r}")


def _period_bounds(moment: datetime, bucket: str) -> tuple[datetime, datetime]:
    """[start, end) of the period containing `moment`, in `moment`'s timezone."""
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "day":
        return day, day + timedelta(days=1)
    if bucket == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    start = day.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month


def net_mmr(
    readings: list[tuple[datetime, int]], start: datetime, end: datetime
) -> int | None:
    """Rating movement the readings can vouch for over [start, end).

    Closing value: the last reading before `end` (a snapshot taken the next
    morning still closes out yesterday). Baseline: the last reading before
    `start`, falling back to the first reading inside the period; one lone
    reading has nothing to diff against and yields None, not 0.
    """
    before_end = [r for r in readings if r[0] < end]
    if not before_end:
        return None
    closing = before_end[-1]
    before_start = [r for r in readings if r[0] < start]
    baseline = before_start[-1] if before_start else before_end[0]
    if baseline is closing:
        return None
    return closing[1] - baseline[1]


def period_stats(db: HistoryDB, bucket: str, tz: tzinfo | None = None) -> list[PeriodRow]:
    """Per-day/week/month results, newest period first."""
    if bucket not in BUCKETS:
        raise ValueError(f"unknown bucket {bucket!r}")
    games = db.conn.execute(
        "SELECT started_at, placement FROM games ORDER BY started_at"
    ).fetchall()
    readings = mmr_series(db, tz)

    grouped: dict[str, list[tuple[datetime, int | None]]] = {}
    for started_at, placement in games:
        moment = _local(started_at, tz)
        grouped.setdefault(_period_key(moment, bucket), []).append((moment, placement))

    rows = []
    for key, members in grouped.items():
        placed = [p for _, p in members if p is not None]
        start, end = _period_bounds(members[0][0], bucket)
        rows.append(PeriodRow(
            period=key,
            games=len(members),
            placed=len(placed),
            avg_placement=sum(placed) / len(placed) if placed else None,
            top4=sum(1 for p in placed if p <= 4),
            firsts=sum(1 for p in placed if p == 1),
            net_mmr=net_mmr(readings, start, end),
        ))
    return sorted(rows, key=lambda r: r.period, reverse=True)


def game_list(db: HistoryDB, tz: tzinfo | None = None) -> list[GameRow]:
    """Every recorded game, newest first."""
    games = db.conn.execute(
        "SELECT id, started_at, hero_card_id, placement, final_turn"
        " FROM games ORDER BY started_at DESC, id DESC"
    ).fetchall()
    readings = mmr_series(db, tz)

    # A game owns a delta only when it is alone between two consecutive
    # readings — two games in one gap share one number that belongs to neither.
    deltas: dict[int, int] = {}
    if len(readings) >= 2:
        moments = [
            (gid, _local(at, tz)) for gid, at, *_ in games
        ]
        for (t0, r0), (t1, r1) in zip(readings, readings[1:]):
            inside = [gid for gid, m in moments if t0 < m < t1]
            if len(inside) == 1:
                deltas[inside[0]] = r1 - r0
    # The post-game screen's own number for its game beats any difference of
    # readings: untracked games in the same gap would be folded into the latter.
    deltas.update(db.conn.execute(
        "SELECT game_id, delta FROM ratings"
        " WHERE game_id IS NOT NULL AND delta IS NOT NULL"
    ).fetchall())

    return [
        GameRow(
            id=gid,
            started_at=_local(at, tz),
            hero_card_id=hero,
            placement=placement,
            final_turn=final_turn,
            mmr_delta=deltas.get(gid),
        )
        for gid, at, hero, placement, final_turn in games
    ]


def game_detail(db: HistoryDB, game_id: int) -> list[CombatRow]:
    """One game's combats in turn order."""
    rows = db.conn.execute(
        "SELECT turn, opponent_hero, predicted_win, predicted_tie, predicted_loss,"
        " outcome, COALESCE(opponent_is_ghost, 0)"
        " FROM combats WHERE game_id = ? ORDER BY turn",
        (game_id,),
    ).fetchall()
    return [
        CombatRow(
            turn=turn,
            opponent_hero=opp,
            predicted_win=win,
            predicted_tie=tie,
            predicted_loss=loss,
            outcome=outcome,
            is_ghost=bool(ghost),
        )
        for turn, opp, win, tie, loss, outcome, ghost in rows
    ]
