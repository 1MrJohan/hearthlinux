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


def test_report_includes_the_trial_distribution():
    """Latency alone can call starvation a speed-up; sample size makes the
    simulator's accuracy cost visible beside it."""
    text = _report([
        _row(50, 51, 100.0, sims=8000),
        _row(40, 41, 300.0, sims=6200),
        _row(30, 31, 2000.0, sims=4000),
    ])
    assert "trials: min 4000" in text, text
    assert "median 6200" in text, text
    assert "max 8000" in text, text


def test_wall_time_is_reported_even_when_nothing_is_comparable():
    """A corpus with no stored predictions still benchmarks fine — the drift
    table is what needs a 'then', not the clock."""
    text = _report([_row(None, 51, 120.0)])
    assert "120" in text


def test_truncated_runs_are_still_called_out():
    # The existing accuracy guard must survive the addition.
    text = _report([_row(50, 51, 100.0, sims=1200)])
    assert "under 4000 trials" in text


def test_brier_then_vs_now_over_decided_combats():
    """CLAUDE.md's accept/reject rule is Brier *direction*, not mean drift —
    so the report must print both numbers instead of leaving the reviewer to
    compute them by hand from a table that only shows the worst 25 rows."""
    rows = [
        Row(turn=5, opponent_hero="H", outcome="win", then=80.0, now=90.0),
        Row(turn=6, opponent_hero="H", outcome="loss", then=40.0, now=20.0),
    ]
    text = _report(rows)
    # then: ((0.8-1)^2 + (0.4-0)^2) / 2 = 0.100
    # now:  ((0.9-1)^2 + (0.2-0)^2) / 2 = 0.025
    assert "Brier then 0.100" in text, text
    assert "now 0.025" in text, text
    assert "2 decided" in text, text


def test_ties_and_unscored_rows_are_excluded_from_brier():
    """A tie can't score a win probability either way, and a row with no
    'now' has nothing to compare — neither may dilute the number."""
    rows = [
        Row(turn=5, opponent_hero="H", outcome="win", then=100.0, now=100.0),
        Row(turn=6, opponent_hero="H", outcome="tie", then=50.0, now=50.0),
        Row(turn=7, opponent_hero="H", outcome=None, then=50.0, now=50.0),
        Row(turn=8, opponent_hero="H", outcome="win", then=60.0, now=None),
    ]
    text = _report(rows)
    assert "Brier then 0.000" in text, text
    assert "1 decided" in text, text


def test_no_brier_line_when_nothing_is_decided():
    text = _report([Row(turn=5, opponent_hero="H", outcome="tie", then=50.0, now=50.0)])
    assert "Brier" not in text, text
