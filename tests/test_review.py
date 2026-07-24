"""history/review.py — the query layer behind the history window.

All rows are hand-inserted with explicit UTC timestamps, and every function is
called with an explicit timezone, so nothing here depends on the machine's
clock or locale.
"""

from datetime import timezone

import pytest

from bgtracker.history.db import HistoryDB
from bgtracker.history import review

UTC = timezone.utc


@pytest.fixture
def db(tmp_path):
    db = HistoryDB(tmp_path / "history.db")
    yield db
    db.close()


def _game(db, started_at, placement=4, hero="HERO_A", final_turn=10, ended=True):
    cur = db.conn.execute(
        "INSERT INTO games (started_at, ended_at, hero_card_id, placement, final_turn)"
        " VALUES (?,?,?,?,?)",
        (
            started_at,
            started_at if ended else None,
            hero,
            placement if ended else None,
            final_turn if ended else None,
        ),
    )
    db.conn.commit()
    return cur.lastrowid


def _rating(db, at, rating):
    db.conn.execute(
        "INSERT INTO ratings (recorded_at, rating) VALUES (?,?)", (at, rating)
    )
    db.conn.commit()


def _combat(db, game_id, turn, outcome="win", win=80.0, ghost=0):
    db.conn.execute(
        "INSERT INTO combats (game_id, turn, opponent_hero, predicted_win,"
        " predicted_tie, predicted_loss, outcome, opponent_is_ghost)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (game_id, turn, "HERO_OPP", win, 10.0, 100 - win - 10.0, outcome, ghost),
    )
    db.conn.commit()


# -- mmr_series ----------------------------------------------------------

def test_mmr_series_is_ascending(db):
    _rating(db, "2026-07-02T10:00:00+00:00", 5100)
    _rating(db, "2026-07-01T10:00:00+00:00", 5000)
    series = review.mmr_series(db)
    assert [r for _, r in series] == [5000, 5100]


def test_mmr_series_empty(db):
    assert review.mmr_series(db) == []


# -- period_stats --------------------------------------------------------

def test_day_buckets_group_and_order_newest_first(db):
    _game(db, "2026-07-01T10:00:00+00:00", placement=1)
    _game(db, "2026-07-01T12:00:00+00:00", placement=5)
    _game(db, "2026-07-02T09:00:00+00:00", placement=3)
    rows = review.period_stats(db, "day", tz=UTC)
    assert [r.period for r in rows] == ["2026-07-02", "2026-07-01"]
    day1 = rows[1]
    assert (day1.games, day1.firsts, day1.top4) == (2, 1, 1)
    assert day1.avg_placement == 3.0


def test_week_and_month_bucket_labels(db):
    _game(db, "2026-07-01T10:00:00+00:00")   # Wednesday, ISO week 27
    _game(db, "2026-07-06T10:00:00+00:00")   # Monday, ISO week 28
    weeks = review.period_stats(db, "week", tz=UTC)
    assert [r.period for r in weeks] == ["2026-W28", "2026-W27"]
    months = review.period_stats(db, "month", tz=UTC)
    assert [r.period for r in months] == ["2026-07"]
    assert months[0].games == 2


def test_unfinished_games_count_but_do_not_skew_placement(db):
    _game(db, "2026-07-01T10:00:00+00:00", placement=2)
    _game(db, "2026-07-01T11:00:00+00:00", ended=False)
    (row,) = review.period_stats(db, "day", tz=UTC)
    assert row.games == 2
    assert row.avg_placement == 2.0
    assert row.top4 == 1


def test_net_mmr_uses_readings_bracketing_the_period(db):
    _rating(db, "2026-06-30T22:00:00+00:00", 5000)  # before the day
    _game(db, "2026-07-01T10:00:00+00:00", placement=1)
    _rating(db, "2026-07-01T12:00:00+00:00", 5060)  # during
    _rating(db, "2026-07-01T23:00:00+00:00", 5100)  # last of the day
    (row,) = review.period_stats(db, "day", tz=UTC)
    assert row.net_mmr == 100


def test_net_mmr_none_when_no_reading_lands_before_the_period_ends(db):
    """One reading opening AND closing a period vouches for nothing.

    The 06-30 snapshot is both the last reading before the day starts and the
    last before it ends; the 07-02 one belongs to the next day. Reporting 0
    would claim the day was flat when the readings say nothing at all.
    """
    _rating(db, "2026-06-30T22:00:00+00:00", 5000)
    _game(db, "2026-07-01T10:00:00+00:00")
    _rating(db, "2026-07-02T09:00:00+00:00", 5200)
    (row,) = review.period_stats(db, "day", tz=UTC)
    assert row.net_mmr is None


def test_net_mmr_falls_back_to_first_reading_inside_the_period(db):
    """No baseline before the period: the first in-period reading is it."""
    _game(db, "2026-07-01T10:00:00+00:00")
    _rating(db, "2026-07-01T09:00:00+00:00", 5000)
    _rating(db, "2026-07-01T20:00:00+00:00", 5150)
    (row,) = review.period_stats(db, "day", tz=UTC)
    assert row.net_mmr == 150


def test_net_mmr_none_without_usable_readings(db):
    _game(db, "2026-07-01T10:00:00+00:00")
    (row,) = review.period_stats(db, "day", tz=UTC)
    assert row.net_mmr is None
    # A single in-period reading has nothing to diff against.
    _rating(db, "2026-07-01T12:00:00+00:00", 5000)
    (row,) = review.period_stats(db, "day", tz=UTC)
    assert row.net_mmr is None


def test_period_stats_empty(db):
    assert review.period_stats(db, "day", tz=UTC) == []


# -- game_list -----------------------------------------------------------

def test_game_list_newest_first_with_fields(db):
    _game(db, "2026-07-01T10:00:00+00:00", placement=2, hero="HERO_A", final_turn=12)
    _game(db, "2026-07-02T10:00:00+00:00", placement=7, hero="HERO_B", final_turn=8)
    rows = review.game_list(db)
    assert [g.hero_card_id for g in rows] == ["HERO_B", "HERO_A"]
    assert rows[0].placement == 7
    assert rows[1].final_turn == 12


def test_game_delta_attributed_only_when_exactly_one_game_between_readings(db):
    _rating(db, "2026-07-01T09:00:00+00:00", 5000)
    lone = _game(db, "2026-07-01T10:00:00+00:00", placement=1)
    _rating(db, "2026-07-01T11:00:00+00:00", 5090)
    # Two games share the next gap: neither may claim its delta.
    a = _game(db, "2026-07-01T12:00:00+00:00", placement=5)
    b = _game(db, "2026-07-01T13:00:00+00:00", placement=6)
    _rating(db, "2026-07-01T14:00:00+00:00", 5010)
    # And one game after the last reading has no closing reading at all.
    open_ended = _game(db, "2026-07-01T15:00:00+00:00", placement=3)

    deltas = {g.id: g.mmr_delta for g in review.game_list(db)}
    assert deltas[lone] == 90
    assert deltas[a] is None and deltas[b] is None
    assert deltas[open_ended] is None


# -- game_detail ---------------------------------------------------------

def test_game_detail_in_turn_order(db):
    gid = _game(db, "2026-07-01T10:00:00+00:00")
    _combat(db, gid, 2, outcome="loss", win=20.0)
    _combat(db, gid, 1, outcome="win", win=75.0)
    _combat(db, gid, 3, outcome="ghost", win=90.0, ghost=1)
    combats = review.game_detail(db, gid)
    assert [c.turn for c in combats] == [1, 2, 3]
    assert combats[0].outcome == "win"
    assert combats[0].predicted_win == 75.0
    assert combats[2].is_ghost is True


def test_game_detail_unknown_game_is_empty(db):
    assert review.game_detail(db, 12345) == []
