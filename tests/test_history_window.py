"""Pure helpers behind the MMR chart; the drawing itself needs a display."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from bgtracker.overlay.history_window import _nice_ticks, _segments  # noqa: E402

T0 = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _points(n: int):
    return [(T0 + timedelta(hours=i), 6000 + i) for i in range(n)]


def test_the_trend_breaks_where_untracked_games_sit():
    """A straight line across a break would claim a history nobody recorded."""
    points = _points(5)
    assert _segments(points, {points[2][0]}) == [[0, 1], [2, 3, 4]]


def test_no_breaks_is_one_run():
    assert _segments(_points(3), set()) == [[0, 1, 2]]


def test_a_break_on_every_reading_leaves_lone_points():
    points = _points(3)
    assert _segments(points, {p[0] for p in points}) == [[0], [1], [2]]


def test_a_tight_spread_never_repeats_a_gridline_label():
    ticks = _nice_ticks(6143, 6145)
    labels = [f"{t:,.0f}" for t in ticks]
    assert len(labels) == len(set(labels))


def test_gridlines_land_on_round_values():
    assert _nice_ticks(6009, 6217) == [6000, 6100, 6200, 6300]
