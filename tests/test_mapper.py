from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor
from bgtracker.sim.mapper import to_battle_info
from dataclasses import replace

from bgtracker.state.game import BoardSnapshot, Minion

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


def test_hand_is_sent_so_start_of_combat_cards_are_simulated():
    """Flighty Scout, Diremuck Forager and friends act from hand.

    Without the hand the simulator sees a smaller board than the one that
    actually fights, which reads as a near-certain loss for a hand-based build.
    """
    snap = snapshot_from_synthetic()
    held = Minion(entity_id=99, card_id="BG32_330", position=0, attack=7, health=7)
    snap = replace(snap, friendly=replace(snap.friendly, hand=(held,)))

    player = to_battle_info(snap)["playerBoard"]["player"]
    [in_hand] = player["hand"]
    assert (in_hand["cardId"], in_hand["attack"], in_hand["health"]) == ("BG32_330", 7, 7)
    assert in_hand["friendly"] is True


def test_opponent_hand_is_never_sent():
    """Their hand is hidden — the cards carry no id, so nothing to project.

    Sending a guessed or empty opponent hand would let the simulator invent
    start-of-combat effects the opponent may not have.
    """
    snap = snapshot_from_synthetic()
    assert snap.opponent.hand == ()
    assert "hand" not in to_battle_info(snap)["opponentBoard"]["player"]


def test_hand_is_omitted_when_the_player_holds_nothing():
    snap = snapshot_from_synthetic()
    snap = replace(snap, friendly=replace(snap.friendly, hand=()))
    assert "hand" not in to_battle_info(snap)["playerBoard"]["player"]
