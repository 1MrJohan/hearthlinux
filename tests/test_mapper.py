from bgtracker.parse import events as ev
from bgtracker.parse.exporter import LiveGameProcessor
from bgtracker.sim.mapper import simulation_blocker, to_battle_info
from dataclasses import replace

from hearthstone.enums import CardType

from bgtracker.state.game import BoardSnapshot, Minion, Secret

from .synthetic import deity_bg_game, minimal_bg_game, secret_bg_game


def snapshot_from_synthetic() -> BoardSnapshot:
    proc = LiveGameProcessor()
    events = proc.feed(minimal_bg_game().lines)
    return next(e for e in events if isinstance(e, ev.CombatStart)).snapshot


def secret_snapshot_from_synthetic() -> BoardSnapshot:
    proc = LiveGameProcessor()
    events = proc.feed(secret_bg_game().lines)
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


def test_secret_is_sent_to_firestone_and_checked_for_compatibility():
    info = to_battle_info(secret_snapshot_from_synthetic())

    assert info["opponentBoard"]["player"]["secrets"] == [{
        "cardId": "TB_Bacon_Secrets_15",
        "entityId": 8,
        "scriptDataNum1": 0,
        "scriptDataNum2": 0,
    }]
    assert info["trackerCombatCardIds"] == ["TB_Bacon_Secrets_15"]


def test_hidden_secret_fails_closed_instead_of_simulating_without_it():
    snap = snapshot_from_synthetic()
    snap = replace(
        snap,
        opponent=replace(snap.opponent, secrets=(Secret(card_id=None, entity_id=8),)),
    )

    assert simulation_blocker(snap) == "combat secret identity hidden"
    assert to_battle_info(snap) is None


def test_deity_secret_carries_countdown_stats_and_which_deity():
    """Shape captured from the first Aberration game: countdown in NUM_1,
    stats in NUM_2/3, the current deity's dbfId (golden Y'Shaarj) in NUM_6."""
    proc = LiveGameProcessor()
    events = proc.feed(deity_bg_game().lines)
    snap = next(e for e in events if isinstance(e, ev.CombatStart)).snapshot

    assert simulation_blocker(snap) is None
    [secret] = to_battle_info(snap)["playerBoard"]["player"]["secrets"]
    assert secret == {
        "cardId": "BG_OldGod",
        "entityId": 9,
        "scriptDataNum1": 3,
        "scriptDataNum2": 248,
        "scriptDataNum3": 254,
        "scriptDataNum6": 134634,
    }


def test_other_secrets_keep_their_payload_even_when_num3_is_set():
    snap = snapshot_from_synthetic()
    snap = replace(
        snap,
        opponent=replace(
            snap.opponent,
            secrets=(Secret(card_id="BG28_603", entity_id=8, num3=2, num6=5),),
        ),
    )

    [secret] = to_battle_info(snap)["opponentBoard"]["player"]["secrets"]
    assert set(secret) == {"cardId", "entityId", "scriptDataNum1", "scriptDataNum2"}


def test_deity_secret_without_its_deity_fails_closed():
    """Rows stored before NUM_3/NUM_6 were read load with 0; the sim would
    awaken a 1-health C'Thun from that rather than the lobby's deity."""
    snap = snapshot_from_synthetic()
    snap = replace(
        snap,
        friendly=replace(
            snap.friendly,
            secrets=(Secret(card_id="BG_OldGod", entity_id=8, num1=3, num2=41),),
        ),
    )

    assert simulation_blocker(snap) == "deity secret incomplete"
    assert to_battle_info(snap) is None


def test_combat_hero_power_fails_closed_instead_of_returning_wrong_odds(monkeypatch):
    """Wingmen was the cause of a recorded 100%-win forecast that actually
    lost: sending no power simulated a different fight. A power whose state is
    not modeled (Embrace Your Rage needs the summoned minion's id) still must
    block rather than be dropped."""
    snap = snapshot_from_synthetic()
    snap = replace(
        snap,
        opponent=replace(snap.opponent, hero_power_id="TB_BaconShop_HP_103"),
    )
    monkeypatch.setattr(
        "bgtracker.sim.mapper.cards.get",
        lambda card_id: {
            "id": card_id,
            "mechanics": ["START_OF_COMBAT", "TRIGGER_VISUAL"],
            "text": "Start of Combat: Summon and get a minion of your Tier.",
        },
    )

    assert simulation_blocker(snap) == (
        "combat hero power not modeled: TB_BaconShop_HP_103"
    )
    assert to_battle_info(snap) is None


def _with_power(snap, side, card_id, **state):
    board = replace(getattr(snap, side), hero_power_id=card_id,
                    hero_power_entity_id=77, **state)
    return replace(snap, **{side: board})


def test_stateless_combat_power_is_sent_instead_of_blocking():
    snap = _with_power(snapshot_from_synthetic(), "opponent", "TB_BaconShop_HP_069")

    assert simulation_blocker(snap) is None
    info = to_battle_info(snap)
    [power] = info["opponentBoard"]["player"]["heroPowers"]
    assert power["cardId"] == "TB_BaconShop_HP_069"
    assert power["entityId"] == 77
    # And the package has to prove it implements it.
    assert "TB_BaconShop_HP_069" in info["trackerCombatCardIds"]
    assert info["playerBoard"]["player"]["heroPowers"] == []


def test_wax_warband_carries_its_buff_and_activation():
    """NUM_3 is the buff; without it the sim silently falls back to +1.

    Used even when ACTIVATED reads False: that is your own recruit-phase
    entity, which never carries the tag, and the power is unconditional."""
    snap = _with_power(snapshot_from_synthetic(), "friendly", "TB_BaconShop_HP_037a",
                       hero_power_activated=False, hero_power_nums=(7, 0, 4, 0, 0, 0))

    [power] = to_battle_info(snap)["playerBoard"]["player"]["heroPowers"]
    assert power["used"] is True
    assert power["info3"] == 4


def test_wax_warband_recorded_before_its_state_fails_closed():
    snap = _with_power(snapshot_from_synthetic(), "friendly", "TB_BaconShop_HP_037a")

    assert simulation_blocker(snap) == (
        "hero power state not recorded: TB_BaconShop_HP_037a"
    )
    assert to_battle_info(snap) is None


def test_rapid_reanimation_is_used_only_when_exhausted():
    snap = snapshot_from_synthetic()
    for used in (True, False):
        s = _with_power(snap, "opponent", "BG25_HERO_103p", hero_power_used=used)
        [power] = to_battle_info(s)["opponentBoard"]["player"]["heroPowers"]
        assert power["used"] is used


def test_snapshot_reads_the_combat_power_state():
    snap = snapshot_from_synthetic()
    for board in (snap.friendly, snap.opponent):
        if board.hero_power_id:
            assert board.hero_power_activated is not None
            assert board.hero_power_nums is not None and len(board.hero_power_nums) == 6


def test_recruit_only_hero_power_does_not_suppress_visible_board_odds(monkeypatch):
    snap = snapshot_from_synthetic()
    snap = replace(
        snap,
        friendly=replace(snap.friendly, hero_power_id="RECRUIT_ONLY_POWER"),
    )
    monkeypatch.setattr(
        "bgtracker.sim.mapper.cards.get",
        lambda card_id: {
            "id": card_id,
            "mechanics": ["TRIGGER_VISUAL"],
            "text": "After you buy a minion, give it +1/+1.",
        },
    )

    assert simulation_blocker(snap) is None
    assert to_battle_info(snap) is not None


def test_current_combat_card_is_flagged_for_sidecar_data_compatibility(monkeypatch):
    """The 1.1.724 pin treated Tasty Lobster as a vanilla stat block because
    Firestone's card-data feed had not published its metadata yet."""
    snap = snapshot_from_synthetic()
    lobster = replace(snap.opponent.minions[0], card_id="BG36_202")
    snap = replace(snap, opponent=replace(snap.opponent, minions=(lobster,)))

    def get_card(card_id):
        if card_id == "BG36_202":
            return {
                "id": card_id,
                "mechanics": ["DEATHRATTLE"],
                "text": "Deathrattle: Give two friendly Beasts +1/+1.",
            }
        return {"id": card_id, "mechanics": [], "text": ""}

    monkeypatch.setattr("bgtracker.sim.mapper.cards.get", get_card)

    assert to_battle_info(snap)["trackerCombatCardIds"] == ["BG36_202"]


def test_economy_only_deathrattle_does_not_block_current_combat(monkeypatch):
    snap = snapshot_from_synthetic()
    barnstormer = replace(snap.opponent.minions[0], card_id="BG26_162")
    snap = replace(snap, opponent=replace(snap.opponent, minions=(barnstormer,)))

    def get_card(card_id):
        if card_id == "BG26_162":
            return {
                "id": card_id,
                "mechanics": ["BATTLECRY", "DEATHRATTLE"],
                "text": "Deathrattle: Give Elementals in the Tavern +8/+8 this game.",
            }
        return {"id": card_id, "mechanics": [], "text": ""}

    monkeypatch.setattr("bgtracker.sim.mapper.cards.get", get_card)

    assert "trackerCombatCardIds" not in to_battle_info(snap)


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


def test_only_revealed_hand_cards_are_projected():
    """Hidden cards carry no id, so they can't be sent even in principle.

    The opponent's hand is mostly hidden but the log does reveal some of it
    during combat, so it is not always empty — what we can see, we send.
    """
    snap = snapshot_from_synthetic()
    assert snap.opponent.hand == ()
    assert "hand" not in to_battle_info(snap)["opponentBoard"]["player"]
    assert all(m.card_id for m in snap.friendly.hand)


def test_hand_is_omitted_when_the_player_holds_nothing():
    snap = snapshot_from_synthetic()
    snap = replace(snap, friendly=replace(snap.friendly, hand=()))
    assert "hand" not in to_battle_info(snap)["playerBoard"]["player"]


def test_only_equipped_trinkets_are_sent():
    """Offers and rejected discoveries pile up in SETASIDE; only PLAY counts.

    Sending the offer pool would hand the simulator a dozen effects the player
    never took.
    """
    snap = snapshot_from_synthetic()
    [equipped] = snap.friendly.trinkets
    assert equipped.card_id == "BG30_MagicItem_988"
    assert equipped.num1 == 3

    [sent] = to_battle_info(snap)["playerBoard"]["player"]["trinkets"]
    assert sent["cardId"] == "BG30_MagicItem_988"
    assert sent["scriptDataNum1"] == 3
    assert sent["entityId"] == equipped.entity_id


def test_trinkets_omitted_when_none_equipped():
    snap = snapshot_from_synthetic()
    assert snap.opponent.trinkets == ()
    assert "trinkets" not in to_battle_info(snap)["opponentBoard"]["player"]


def test_trinkets_are_not_mistaken_for_minions():
    # A trinket sitting in PLAY must not end up on the board.
    snap = snapshot_from_synthetic()
    assert all(m.card_id != "BG30_MagicItem_988" for m in snap.friendly.minions)


def test_trinket_shop_placeholders_are_not_equipment():
    """"The Greater Trinket Shop opens in 8 turns!" is an announcement.

    It sits in play carrying the trinket card type, so only the id keeps it out
    of the payload. The number is per-set, so the match generalizes across set
    numbering rather than naming one set and rotting when the next one ships.
    """
    from bgtracker.state.game import _TRINKET_PLACEHOLDER_RE, _is_trinket

    class FakeCard:
        def __init__(self, card_id, type_=CardType.BATTLEGROUND_TRINKET):
            self.card_id = card_id
            self.type = type_

    assert not _is_trinket(FakeCard("BG30_Trinket_1st"))
    assert not _is_trinket(FakeCard("BG30_Trinket_2nd"))
    assert not _is_trinket(FakeCard("BG34_Trinket_1st"))   # the next set's placeholders
    assert not _is_trinket(FakeCard("BG99_Trinket_2nd"))   # and any future numbering
    assert _is_trinket(FakeCard("BG32_MagicItem_893"))     # Bluegill Flippers

    # Anchored: the "Buy a … Trinket" pool spells (…_Spell) are not placeholders;
    # they are excluded as pool spells elsewhere and must not be caught here.
    assert _TRINKET_PLACEHOLDER_RE.match("BG34_Trinket_1st")
    assert not _TRINKET_PLACEHOLDER_RE.match("BG34_Trinket_1st_Spell")

    # Hidden opponent cards sit in PLAY carrying no id; the placeholder match
    # must not crash on None the way a bare regex .match(None) would.
    assert not _is_trinket(FakeCard(None, CardType.MINION))


def test_minion_tavern_tier_is_sent():
    """Several combat effects key on a minion's tier (Captain Sanders filters
    on `e.tavernTier <= 6`, avenge summons scale by it). The simulator reads
    the field directly with no card-DB fallback, so omitting it makes those
    comparisons silently false rather than approximate."""
    info = to_battle_info(snapshot_from_synthetic())
    [mine] = info["playerBoard"]["board"]
    assert mine["tavernTier"] == 1
