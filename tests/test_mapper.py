from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor
from bgtracker.sim.mapper import to_battle_info
from bgtracker.state.game import BoardSnapshot

from .synthetic import minimal_bg_game


def snapshot_from_synthetic() -> BoardSnapshot:
    proc = LiveGameProcessor()
    events = proc.feed(minimal_bg_game().lines)
    return next(e for e in events if isinstance(e, ev.CombatStart)).snapshot


def test_maps_snapshot_to_battle_info():
    info = to_battle_info(snapshot_from_synthetic())
    assert info is not None
    assert info["gameState"]["currentTurn"] == 1

    player = info["playerBoard"]
    assert player["player"]["cardId"] == "TB_BaconShop_HERO_11"
    assert player["player"]["hpLeft"] == 40
    assert player["player"]["tavernTier"] == 2
    [mine] = player["board"]
    assert (mine["cardId"], mine["attack"], mine["health"]) == ("BG_EX1_506", 2, 3)
    assert mine["friendly"] is True

    [theirs] = info["opponentBoard"]["board"]
    assert theirs["taunt"] is True
    assert theirs["friendly"] is False


def test_incomplete_snapshot_maps_to_none():
    snap = BoardSnapshot(turn=1, friendly=None, opponent=None)
    assert to_battle_info(snap) is None
