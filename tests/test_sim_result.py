"""SimResult arithmetic — no sidecar, no node."""

from bgtracker.sim.client import SimResult


def _result(win=63.0, sims_run=8000):
    return SimResult(
        won_percent=win, tied_percent=9.0, lost_percent=100.0 - win - 9.0,
        avg_damage_won=14.0, avg_damage_lost=9.0, sims_run=sims_run,
    )


def test_margin_shrinks_as_the_sample_grows():
    """A 63% off 800 trials and one off 8000 must not read the same.

    simulateBattle abandons the run when it exceeds maxAcceptableDuration, so a
    heavy late-game board really can return a fraction of the requested trials.
    """
    assert _result(sims_run=800).margin > _result(sims_run=8000).margin
    assert round(_result(sims_run=8000).margin, 1) == 1.1


def test_margin_is_unknown_without_a_sample():
    """Zero trials is no information, not perfect precision."""
    assert _result(sims_run=0).margin is None


def test_a_certain_outcome_has_no_spread():
    assert _result(win=100.0, sims_run=8000).margin == 0.0


def test_damage_taken_reads_as_a_range_when_one_is_known():
    """The tail is what eliminates you, so the spread beats the average."""
    r = _result()
    r.damage_lost_range = (9.0, 17.0)
    assert r.damage_taken_text == "9–17"


def test_damage_taken_falls_back_to_the_average():
    """Older sidecars and pooled multi-worker runs may not supply a range."""
    r = _result()
    r.damage_lost_range = None
    assert r.damage_taken_text == "9"


def test_a_degenerate_range_collapses_to_one_number():
    """Every trial dealt the same damage — '9–9' is just noise on screen."""
    r = _result()
    r.damage_lost_range = (9.0, 9.0)
    assert r.damage_taken_text == "9"
