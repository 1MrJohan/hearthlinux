import asyncio
import json
import sqlite3

from bgtracker.app import Pipeline, _apply_damage_cap, _classify_outcome
from bgtracker.sim.client import SimResult
from bgtracker.history.db import HistoryDB, _board_json, board_from_json
from bgtracker.history.stats import report
from bgtracker.parse.exporter import LiveGameProcessor
from bgtracker.state.game import BoardSnapshot, Enchantment, Minion, PlayerBoard, Trinket

from .synthetic import minimal_bg_game


def _board(pid, hp, armor=0, hero="H", bg_pid=None):
    return PlayerBoard(player_id=pid, bg_player_id=pid if bg_pid is None else bg_pid,
                       hero_card_id=hero, hero_entity_id=1,
                       health=hp, armor=armor, tier=1)


def test_classify_outcome():
    start = BoardSnapshot(turn=1, friendly=_board(1, 40), opponent=_board(2, 30))
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 35), _board(2, 30))) == "loss"
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 40), _board(2, 25))) == "win"
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 40), _board(2, 30))) == "tie"
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 40), None)) is None


def test_a_different_opponent_after_combat_is_not_a_tie():
    """Identity is bg_player_id, never player_id.

    Opponents all share CONTROLLER slot 14, so matching on player_id passes for
    *any* opponent — and a stranger's untouched HP then reads as a damage-free
    tie. Unknown is the honest answer.
    """
    start = BoardSnapshot(turn=1, friendly=_board(1, 40), opponent=_board(14, 30, bg_pid=2))
    same_slot_other_player = BoardSnapshot(1, _board(1, 40), _board(14, 30, bg_pid=5))
    assert _classify_outcome(start, same_slot_other_player) is None


def test_beating_the_ghost_is_not_recorded_as_a_tie():
    """Kel'Thuzad is the odd-player-out ghost, and ghost fights are damage-free.

    Nobody's HP moves when you beat a ghost, so the HP-delta test cannot tell a
    win from a tie: all 13 ghost fights in the recorded history landed as ties
    and dragged the 90-100%% calibration bucket down from 86%% to 75%%. They get
    their own outcome and are excluded from calibration.
    """
    ghost = _board(2, 30, hero="TB_BaconShop_HERO_KelThuzad")
    start = BoardSnapshot(turn=1, friendly=_board(1, 40), opponent=ghost)
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 40), ghost)) == "ghost"


def test_losing_to_the_ghost_is_still_a_real_loss():
    """Only the damage-free result is ambiguous. Ghosts do hit back, and HP you
    actually lost is a genuine, calibratable loss."""
    ghost = _board(2, 30, hero="TB_BaconShop_HERO_KelThuzad")
    start = BoardSnapshot(turn=1, friendly=_board(1, 40), opponent=ghost)
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 33), ghost)) == "loss"


def test_the_damage_cap_rules_out_lethal():
    """Early-game combat damage is capped, and the simulator doesn't model it.

    If the cap sits below your total health you cannot die this fight, whatever
    the uncapped run counted as lethal — so the warning must not fire.
    """
    snap = BoardSnapshot(turn=3, friendly=_board(1, 25), opponent=_board(2, 30), damage_cap=6)
    result = SimResult(
        won_percent=10, tied_percent=0, lost_percent=90,
        avg_damage_won=12, avg_damage_lost=14, sims_run=8000,
        lost_lethal_percent=40.0, damage_lost_range=(9.0, 20.0),
    )
    _apply_damage_cap(result, snap)
    assert result.lost_lethal_percent == 0.0
    assert result.avg_damage_lost == 6
    assert result.damage_lost_range == (6, 6)


def test_a_cap_above_your_health_still_allows_lethal():
    """At 4 HP behind a cap of 6, the fight really can end you."""
    snap = BoardSnapshot(turn=8, friendly=_board(1, 4), opponent=_board(2, 30), damage_cap=6)
    result = SimResult(
        won_percent=10, tied_percent=0, lost_percent=90,
        avg_damage_won=12, avg_damage_lost=14, sims_run=8000,
        lost_lethal_percent=40.0,
    )
    _apply_damage_cap(result, snap)
    assert result.lost_lethal_percent == 40.0


def _ghost_row(db, outcome):
    game = db.start_game("g1")
    db.conn.execute(
        "INSERT INTO combats (game_id, turn, opponent_hero, outcome) VALUES (?,?,?,?)",
        (game, 5, "TB_BaconShop_HERO_KelThuzad", outcome),
    )
    db.conn.commit()


def test_migration_adds_the_sample_size_columns(tmp_path):
    db = HistoryDB(tmp_path / "history.db")
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(combats)")}
    assert {"sims_run", "sim_ms", "predicted_lost_lethal"} <= cols


def test_ghost_ties_recorded_before_the_fix_are_backfilled(tmp_path):
    path = tmp_path / "history.db"
    db = HistoryDB(path)
    _ghost_row(db, "tie")
    db.conn.execute("PRAGMA user_version = 0")  # pretend it predates the migration
    db.conn.commit()
    db.close()

    assert HistoryDB(path).conn.execute("SELECT outcome FROM combats").fetchone()[0] == "ghost"


def test_the_ghost_backfill_never_clobbers_a_real_loss(tmp_path):
    """Losing to a ghost costs real HP and stays a real, calibratable loss.

    The backfill only rewrites 'tie', and is gated on user_version so it cannot
    run again over rows written after the fix.
    """
    path = tmp_path / "history.db"
    db = HistoryDB(path)
    _ghost_row(db, "loss")
    db.conn.execute("PRAGMA user_version = 0")
    db.conn.commit()
    db.close()

    assert HistoryDB(path).conn.execute("SELECT outcome FROM combats").fetchone()[0] == "loss"


def test_a_damaging_ghost_fight_is_recorded_as_a_real_loss(tmp_path):
    """A ghost can hit back, and HP you actually lost is a real loss."""
    db = HistoryDB(tmp_path / "history.db")
    game = db.start_game("g1")
    snap = BoardSnapshot(
        turn=9, friendly=_board(1, 20),
        opponent=_board(2, 0, hero="TB_BaconShop_HERO_KelThuzad"),
    )
    db.record_combat(game, snap, None, "loss")
    outcome, ghost = db.conn.execute(
        "SELECT outcome, opponent_is_ghost FROM combats"
    ).fetchone()
    assert outcome == "loss"
    assert ghost == 1, "the fight is still against a ghost"


def test_ghost_fights_are_excluded_whether_or_not_they_hurt(tmp_path):
    """Excluding only the ambiguous ones would bias the table toward losses.

    A ghost fight scores as a loss when it costs HP but is unscoreable
    otherwise, since a win against a ghost is indistinguishable from a tie.
    Keeping the losses and dropping the rest is a selection effect, so the whole
    category stays out of calibration however it went.
    """
    path = tmp_path / "history.db"
    db = HistoryDB(path)
    game = db.start_game("g1")
    ghost = _board(2, 0, hero="TB_BaconShop_HERO_KelThuzad")
    prediction = SimResult(won_percent=95, tied_percent=0, lost_percent=5,
                           avg_damage_won=10, avg_damage_lost=4, sims_run=8000)
    db.record_combat(game, BoardSnapshot(turn=9, friendly=_board(1, 20), opponent=ghost),
                     prediction, "loss")
    db.record_combat(game, BoardSnapshot(turn=10, friendly=_board(1, 20), opponent=ghost),
                     prediction, "ghost")
    db.close()

    out = report(path)
    assert "2 ghost fight(s) excluded" in out
    assert "(no recorded combats with predictions)" in out


def test_the_report_migrates_a_stale_database(tmp_path):
    """The report must not read a pre-migration schema.

    It used to open a bare sqlite3 connection, so migrations — which only ran on
    the write path — never fired for `bgtracker stats`, and the calibration
    table went on counting ghost fights as ties.
    """
    path = tmp_path / "history.db"
    db = HistoryDB(path)
    _ghost_row(db, "tie")
    db.conn.execute("PRAGMA user_version = 0")
    db.conn.commit()
    db.close()

    out = report(path)
    assert "ghost fight" in out
    assert HistoryDB(path).conn.execute("SELECT outcome FROM combats").fetchone()[0] == "ghost"


def test_a_stored_board_round_trips():
    """Stored snapshots are the offline repro corpus — they must come back whole.

    Nested enchantments are the part that matters: a minion's printed stats are
    almost never its real ones, so a board that loses them re-simulates wrong.
    """
    board = PlayerBoard(
        player_id=1, bg_player_id=3, hero_card_id="H", hero_entity_id=1,
        health=30, armor=5, tier=4,
        minions=(Minion(entity_id=9, card_id="CS2_065", position=1, attack=7, health=9,
                        taunt=True, golden=True,
                        enchantments=(Enchantment(card_id="E1", num1=2, num2=3),)),),
        hand=(Minion(entity_id=10, card_id="BG_X", position=0, attack=1, health=1),),
        trinkets=(Trinket(card_id="T1", entity_id=11, num1=1),),
        global_info={"BloodGemAttackBonus": 2},
    )
    assert board_from_json(_board_json(board)) == board


def test_a_board_from_an_older_schema_still_loads():
    """Rows predating hand/trinkets/global_info are the bulk of the corpus."""
    legacy = json.dumps({
        "player_id": 1, "bg_player_id": 1, "hero_card_id": "H", "hero_entity_id": 1,
        "health": 30, "armor": 0, "tier": 2,
        "minions": [{"entity_id": 2, "card_id": "C", "position": 1, "attack": 1, "health": 1}],
    })
    board = board_from_json(legacy)
    assert board.hand == () and board.trinkets == () and board.global_info == {}
    assert board.minions[0].enchantments == ()


def test_pipeline_records_game(tmp_path):
    db = HistoryDB(tmp_path / "history.db")
    pipeline = Pipeline(sim=None, db=db)
    processor = LiveGameProcessor()
    asyncio.run(pipeline.handle(processor.feed(minimal_bg_game().lines)))

    games = db.conn.execute(
        "SELECT hero_card_id, placement, final_turn FROM games"
    ).fetchall()
    assert games == [("TB_BaconShop_HERO_11", 3, 1)]

    combats = db.conn.execute(
        "SELECT turn, opponent_hero, outcome FROM combats"
    ).fetchall()
    assert len(combats) == 1
    turn, opp_hero, outcome = combats[0]
    assert turn == 1 and opp_hero == "TB_BaconShop_HERO_22"
    # synthetic log doesn't change HP during combat -> tie
    assert outcome == "tie"


# -- resim reads through the migrations --------------------------------
def test_resim_skips_ghost_fights_on_an_unmigrated_database(tmp_path):
    """resim must open through HistoryDB, never a bare sqlite3.connect.

    The ghost flag it filters on is created by a migration. A reader that
    skipped them would find no `opponent_is_ghost` column (and, before
    user_version 1, no 'ghost' outcome either) and quietly resim ghost fights —
    exactly the failure stats.py carries a comment warning about.
    """
    from bgtracker.history.resim import _rows

    path = tmp_path / "history.db"
    db = HistoryDB(path)
    game = db.start_game("g1")
    for turn, hero in ((5, "TB_BaconShop_HERO_KelThuzad"), (6, "TB_BaconShop_HERO_22")):
        db.conn.execute(
            "INSERT INTO combats (game_id, turn, opponent_hero, outcome,"
            " predicted_win, my_board, opp_board) VALUES (?,?,?,?,?,?,?)",
            (game, turn, hero, "tie", 50.0, "{}", "{}"),
        )
    # Strip every trace of the migrations, as a database written before them.
    db.conn.execute("UPDATE combats SET opponent_is_ghost = NULL")
    db.conn.execute("PRAGMA user_version = 0")
    db.conn.commit()
    db.close()

    # A bare connection is what resim used to open: no migration runs, the
    # ghost row is unflagged, and it sails straight through the filter.
    raw = sqlite3.connect(path)
    assert len(_rows(raw, None)) == 2, "precondition: unmigrated, both rows visible"
    raw.close()

    # Through HistoryDB the migration flags the ghost first, so it drops out.
    migrated = HistoryDB(path)
    rows = _rows(migrated.conn, None)
    migrated.close()
    assert [r[1] for r in rows] == ["TB_BaconShop_HERO_22"]


def test_resim_also_drops_a_ghost_fight_that_cost_hp(tmp_path):
    """The flag, not the outcome. A ghost loss is a genuine loss and records one,
    but its prediction still cannot be scored — same predicate stats.py uses."""
    from bgtracker.history.resim import _rows

    db = HistoryDB(tmp_path / "history.db")
    game = db.start_game("g1")
    ghost = _board(2, 0, hero="TB_BaconShop_HERO_KelThuzad")
    prediction = SimResult(won_percent=95, tied_percent=0, lost_percent=5,
                           avg_damage_won=10, avg_damage_lost=4, sims_run=8000)
    db.record_combat(game, BoardSnapshot(turn=9, friendly=_board(1, 20), opponent=ghost),
                     prediction, "loss")
    assert _rows(db.conn, None) == []
    db.close()


def test_re_recording_without_a_prediction_keeps_the_stored_one(tmp_path):
    """Catch-up replays a session from the top and re-records every combat.
    Once it stops simulating them, the second write carries prediction=None —
    and the calibration table is built from exactly those columns."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    snap = BoardSnapshot(turn=5, friendly=_board(1, 40), opponent=_board(2, 30))
    db.record_combat(game, snap, SimResult(
        won_percent=61, tied_percent=4, lost_percent=35,
        avg_damage_won=9, avg_damage_lost=7, sims_run=8000, sim_ms=412.0,
        lost_lethal_percent=3.0,
    ), "win")

    db.record_combat(game, snap, None, "win")

    row = db.conn.execute(
        "SELECT predicted_win, sims_run, sim_ms, predicted_lost_lethal,"
        " outcome FROM combats WHERE game_id=? AND turn=5", (game,)
    ).fetchone()
    assert row == (61, 8000, 412.0, 3.0, "win")
    db.close()


def test_re_recording_still_refreshes_the_boards(tmp_path):
    """Boards are projections of the same log, so the newer read wins — only
    the prediction columns are the ones a null must never blank."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    first = BoardSnapshot(turn=5, friendly=_board(1, 40), opponent=_board(2, 30))
    second = BoardSnapshot(turn=5, friendly=_board(1, 40), opponent=_board(2, 22))
    db.record_combat(game, first, None, None)
    db.record_combat(game, second, None, "loss")

    row = db.conn.execute(
        "SELECT opp_board, outcome FROM combats WHERE game_id=? AND turn=5", (game,)
    ).fetchone()
    assert json.loads(row[0])["health"] == 22
    assert row[1] == "loss"
    db.close()


def test_a_game_ends_once(tmp_path):
    """end_game stamped ended_at with _now() on every write, so replaying a
    finished session moved its end time to the replay's clock — 14 games in the
    author's database claim durations over three hours, their end times
    clustered seconds apart on the evening they were replayed."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    db.end_game(game, placement=3, final_turn=24)
    first = db.conn.execute("SELECT ended_at FROM games WHERE id=?", (game,)).fetchone()[0]

    db.end_game(game, placement=3, final_turn=24)
    again = db.conn.execute("SELECT ended_at FROM games WHERE id=?", (game,)).fetchone()[0]
    assert again == first


def test_re_ending_without_a_placement_keeps_the_recorded_one(tmp_path):
    """GameEnd is deferred until a placement tag appears, but finalize() can
    flush one carrying None. On catch-up that would blank a real placement."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    db.end_game(game, placement=2, final_turn=24)
    db.end_game(game, placement=None, final_turn=24)
    assert db.conn.execute(
        "SELECT placement FROM games WHERE id=?", (game,)
    ).fetchone()[0] == 2
    db.close()


def test_a_later_real_prediction_replaces_an_earlier_one(tmp_path):
    """Pins the direction of the COALESCE guard, not just its presence.
    test_re_recording_without_a_prediction_keeps_the_stored_one only writes
    a real value once, so COALESCE(excluded.X, X) and the reversed
    COALESCE(X, excluded.X) both pass it — they only disagree when the old
    value is non-NULL and the new one is also non-NULL. Without this test a
    reversed guard would freeze the first prediction forever and silently
    drop every later real one, which is the same class of data loss this
    task exists to prevent, just pointing the other way."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    snap = BoardSnapshot(turn=5, friendly=_board(1, 40), opponent=_board(2, 30))
    db.record_combat(game, snap, SimResult(
        won_percent=61, tied_percent=4, lost_percent=35,
        avg_damage_won=9, avg_damage_lost=7, sims_run=8000, sim_ms=412.0,
        lost_lethal_percent=3.0,
    ), "win")
    db.record_combat(game, snap, SimResult(
        won_percent=74, tied_percent=6, lost_percent=20,
        avg_damage_won=11, avg_damage_lost=5, sims_run=12000, sim_ms=530.0,
        lost_lethal_percent=1.0,
    ), "win")

    row = db.conn.execute(
        "SELECT predicted_win, sims_run, sim_ms FROM combats"
        " WHERE game_id=? AND turn=5", (game,)
    ).fetchone()
    assert row == (74, 12000, 530.0)
    db.close()
