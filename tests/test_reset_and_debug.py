"""Destructive resets, the reusable doctor, and the diagnostics bundle.

The history reset is the only irreversible thing the tracker can do to you, so
most of what is pinned down here is that it keeps working afterwards: an
emptied database that the live pipeline can no longer write to would look
exactly like a working one until you next checked your stats.
"""

from __future__ import annotations

import asyncio

import pytest

from bgtracker import doctor
from bgtracker.config import Config
from bgtracker.history.db import HistoryDB
from bgtracker.sim.client import SimResult
from bgtracker.state.game import BoardSnapshot, Minion, PlayerBoard


def _board() -> PlayerBoard:
    return PlayerBoard(
        player_id=1, bg_player_id=1, hero_card_id="TB_BaconShop_HERO_34",
        hero_entity_id=1, health=20, armor=0, tier=3,
        minions=(Minion(entity_id=2, card_id="CS2_065", position=1, attack=1, health=3),),
    )


def _populated(tmp_path) -> HistoryDB:
    db = HistoryDB(tmp_path / "history.db")
    game = db.start_game("log-1")
    db.set_hero(game, "TB_BaconShop_HERO_34")
    db.record_combat(
        game,
        BoardSnapshot(turn=5, friendly=_board(), opponent=_board()),
        SimResult(won_percent=60, tied_percent=10, lost_percent=30,
                  avg_damage_won=8, avg_damage_lost=4),
        "win",
    )
    db.end_game(game, placement=2, final_turn=12)
    return db


# -- history reset -----------------------------------------------------
def test_counts_report_what_is_at_stake(tmp_path):
    db = _populated(tmp_path)
    assert db.counts() == (1, 1)
    db.close()


def test_reset_empties_the_database(tmp_path):
    db = _populated(tmp_path)
    db.reset(backup=False)
    assert db.counts() == (0, 0)
    db.close()


def test_reset_leaves_a_usable_connection(tmp_path):
    """The reason this reopens instead of unlinking under a live handle.

    SQLite keeps writing happily into a deleted inode, so a tracker that had
    its database removed from under it would look like it was still recording
    and silently not be.
    """
    db = _populated(tmp_path)
    db.reset(backup=False)
    game = db.start_game("log-2")
    db.record_combat(
        game,
        BoardSnapshot(turn=1, friendly=_board(), opponent=_board()),
        None,
        "loss",
    )
    assert db.counts() == (1, 1)
    db.close()


def test_reset_backs_up_by_default(tmp_path):
    db = _populated(tmp_path)
    saved = db.reset()
    assert saved is not None and saved.is_file()
    db.close()

    # The backup is a real database, still holding what was thrown away.
    restored = HistoryDB(saved)
    assert restored.counts() == (1, 1)
    restored.close()


def test_reset_without_backup_leaves_no_copy(tmp_path):
    db = _populated(tmp_path)
    assert db.reset(backup=False) is None
    db.close()
    assert list(tmp_path.glob("*.bak-*")) == []


def test_reset_on_a_fresh_database_is_harmless(tmp_path):
    db = HistoryDB(tmp_path / "history.db")
    assert db.reset(backup=True) is None   # nothing to back up
    assert db.counts() == (0, 0)
    db.close()


def test_pipeline_reset_forgets_the_game_being_recorded(tmp_path):
    from bgtracker.app import Pipeline

    db = _populated(tmp_path)
    pipeline = Pipeline(sim=None, db=db)
    pipeline._game_id = 1
    pipeline.reset_history(backup=False)
    # The row it was appending combats to no longer exists; keeping the id
    # would write orphans against a foreign key that is gone.
    assert pipeline._game_id is None
    assert db.counts() == (0, 0)
    db.close()


def test_pipeline_reset_without_a_database_does_nothing():
    from bgtracker.app import Pipeline

    assert Pipeline(sim=None, db=None).reset_history() is None


# -- doctor as data ----------------------------------------------------
def test_doctor_yields_records_with_known_kinds():
    records = asyncio.run(doctor.collect(Config()))
    assert records, "the doctor found nothing to say at all"
    for kind, text in records:
        assert kind in doctor.PREFIXES, f"unknown record kind {kind!r}"
        assert isinstance(text, str) and text


def test_doctor_formatting_matches_the_cli_prefixes():
    formatted = doctor.format_records([
        ("plain", "config file: x"),
        ("head", "install:"),
        ("info", "found: y"),
        ("ok", "using: y"),
        ("bad", "nope"),
    ])
    assert formatted.splitlines() == [
        "config file: x",
        "install:",
        f"{doctor.INFO}found: y",
        f"{doctor.OK}using: y",
        f"{doctor.BAD}nope",
    ]


def test_the_install_check_reports_its_verdict_once():
    """Resolving is what logs "Using Hearthstone install".

    Doing it a second time to find out the answer put a duplicate line in the
    doctor's output, which is user-visible.
    """
    records, _hs_dir = doctor._check_install(Config())
    usings = [text for kind, text in records if kind == "ok" and text.startswith("using:")]
    assert len(usings) <= 1


# -- status ------------------------------------------------------------
def test_status_rows_survive_a_tracker_that_has_done_nothing():
    from bgtracker.diagnostics import Status

    rows = Status().rows()
    assert all(len(row) == 3 for row in rows)
    labels = [label for label, _v, _s in rows]
    assert "Simulator" in labels and "Log session" in labels
    # No session yet is a problem worth colouring, not a neutral fact.
    assert dict((label, state) for label, _v, state in rows)["Log session"] == "bad"


def test_status_notes_what_the_pipeline_reports():
    from bgtracker.diagnostics import Status

    status = Status()
    status.note_lines(120)
    status.note_lines(80)
    status.note_event("CombatStart")
    status.note_sim(SimResult(
        won_percent=60, tied_percent=10, lost_percent=30,
        avg_damage_won=8, avg_damage_lost=4, sims_run=8000, sim_ms=412.0,
    ))
    assert status.lines_read == 200
    assert status.last_event == "CombatStart"
    assert status.last_sims_run == 8000
    values = {label: value for label, value, _ in status.rows()}
    assert "8000 trials in 412ms" in values["Last simulation"]


# -- the bundle --------------------------------------------------------
def test_the_bundle_collects_every_section(tmp_path, monkeypatch):
    import bgtracker.diagnostics as diag

    monkeypatch.setattr(diag, "CACHE_DIR", tmp_path)
    path = asyncio.run(diag.bundle(Config(), extra="a note"))
    body = path.read_text()
    for section in ("versions", "paths", "config.toml", "doctor", "status",
                    "recent log", "notes"):
        assert f"===== {section} =====" in body, f"bundle is missing {section}"
    assert "a note" in body


def test_a_failing_doctor_does_not_lose_the_rest_of_the_bundle(tmp_path, monkeypatch):
    import bgtracker.diagnostics as diag

    monkeypatch.setattr(diag, "CACHE_DIR", tmp_path)

    async def explode(_cfg=None):
        raise RuntimeError("no game here")

    monkeypatch.setattr(doctor, "collect", explode)
    body = asyncio.run(diag.bundle(Config())).read_text()
    # The environment detail is exactly what is wanted when the checks
    # themselves are what broke.
    assert "doctor failed" in body
    assert "===== versions =====" in body


@pytest.mark.parametrize("enabled", [True, False])
def test_log_file_handler_follows_the_setting(enabled, tmp_path, monkeypatch):
    import logging

    from bgtracker import logging_setup

    monkeypatch.setattr(logging_setup, "LOG_FILE", tmp_path / "bgtracker.log")
    monkeypatch.setattr(logging_setup, "_file_handler", None)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logging_setup.apply_config(Config(log_to_file=enabled))
        assert (tmp_path / "bgtracker.log").is_file() is enabled
    finally:
        logging_setup.apply_config(Config(log_to_file=False))
        root.handlers[:] = before


def test_log_level_setting_reaches_the_root_logger(monkeypatch):
    import logging

    from bgtracker import logging_setup

    monkeypatch.setattr(logging_setup, "_verbose", False)
    root = logging.getLogger()
    before = root.level
    try:
        logging_setup.apply_config(Config(log_level="debug", log_to_file=False))
        assert root.level == logging.DEBUG
        logging_setup.apply_config(Config(log_level="warning", log_to_file=False))
        assert root.level == logging.WARNING
    finally:
        root.setLevel(before)


def test_verbose_is_a_floor_the_setting_cannot_undercut(monkeypatch):
    import logging

    from bgtracker import logging_setup

    monkeypatch.setattr(logging_setup, "_verbose", True)
    # -v on the command line must not be silently overridden by a quiet config.
    assert logging_setup.level_for(Config(log_level="warning")) == logging.DEBUG
