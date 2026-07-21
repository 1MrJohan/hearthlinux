import asyncio

from bgtracker.app import Pipeline, _classify_outcome
from bgtracker.history.db import HistoryDB
from bgtracker.parse.exporter import LiveGameProcessor
from bgtracker.state.game import BoardSnapshot, PlayerBoard

from .synthetic import minimal_bg_game


def _board(pid, hp, armor=0):
    return PlayerBoard(player_id=pid, hero_card_id="H", hero_entity_id=1,
                       health=hp, armor=armor, tier=1)


def test_classify_outcome():
    start = BoardSnapshot(turn=1, friendly=_board(1, 40), opponent=_board(2, 30))
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 35), _board(2, 30))) == "loss"
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 40), _board(2, 25))) == "win"
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 40), _board(2, 30))) == "tie"
    assert _classify_outcome(start, BoardSnapshot(1, _board(1, 40), None)) is None


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
