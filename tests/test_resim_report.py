"""The resim report doubles as the simulator's throughput bench.

It already replays stored boards through the live sidecar serially, which is
the benchmark loop. It just never said how long they took.
"""

from __future__ import annotations

from bgtracker.history.resim import Row, _report


def _row(then, now, ms, sims=8000):
    return Row(turn=5, opponent_hero="H", outcome="win", then=then, now=now,
               sims_run=sims, sim_ms=ms)


def test_report_includes_the_wall_time_distribution():
    text = _report([_row(50, 51, 100.0), _row(40, 41, 300.0), _row(30, 31, 2000.0)])
    assert "mean 800" in text or "mean 800.0" in text, text
    assert "max 2000" in text, text


def test_wall_time_is_reported_even_when_nothing_is_comparable():
    """A corpus with no stored predictions still benchmarks fine — the drift
    table is what needs a 'then', not the clock."""
    text = _report([_row(None, 51, 120.0)])
    assert "120" in text


def test_truncated_runs_are_still_called_out():
    # The existing accuracy guard must survive the addition.
    text = _report([_row(50, 51, 100.0, sims=1200)])
    assert "under 4000 trials" in text
