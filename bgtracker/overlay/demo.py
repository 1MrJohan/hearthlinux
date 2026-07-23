"""Fake event feed for working on the overlay without a live game.

`python -m bgtracker --overlay --demo` cycles hero select -> combat -> shop
using the sample lineup from the design handoff, so every state the skin has to
cover — golden minion, eliminated player, YOU row, an unscouted opponent — is
reachable on demand instead of waiting for a real match to produce it.

The events are the same `bgtracker.parse.events` types the live parser emits
and go through `OverlayApp.on_event` unchanged, so this exercises the real
wiring rather than a parallel rendering path.
"""

from __future__ import annotations

import asyncio
import itertools

from bgtracker.parse import events as ev
from bgtracker.sim.client import SimResult
from bgtracker.state.game import BoardSnapshot, Minion, PlayerBoard

# Hero art ids verified against the HearthstoneJSON CDN in the handoff.
HEROES = [
    (1, "TB_BaconShop_HERO_34", 56, False, False),   # Patchwerk
    (2, "TB_BaconShop_HERO_52", 41, False, False),   # Deathwing
    (3, "TB_BaconShop_HERO_43", 18, False, True),    # Dinotamer Brann — you
    (4, "TB_BaconShop_HERO_94", 27, False, False),   # Tickatus
    (5, "TB_BaconShop_HERO_16", 22, False, False),   # A. F. Kay
    (6, "TB_BaconShop_HERO_56", 15, False, False),   # Alexstrasza
    (7, "TB_BaconShop_HERO_23", 9, False, False),    # Shudderwock
    (8, "TB_BaconShop_HERO_67", 0, True, False),     # Captain Hooktusk — out
]

# (card_id, attack, health, keyword flags, golden)
# Names below are what the card DB actually returns for these ids — a few of
# the handoff's labels were off (GVG_021 is Mal'Ganis, not Voidcaller; OG_122
# is Mukla; BOT_312 is Replicating Menace).
DEMONS = [
    ("LOOT_013", 2, 4, {"taunt": True}, False),      # Vulgar Homunculus
    ("BRM_006", 2, 4, {}, False),                    # Imp Gang Boss
    ("CS2_065", 1, 3, {"taunt": True}, False),       # Voidwalker
    ("GVG_021", 3, 4, {"divine_shield": True}, False),  # Mal'Ganis
    ("LOOT_368", 3, 9, {"taunt": True, "reborn": True}, False),  # Voidlord
    ("OG_122", 9, 7, {"windfury": True}, True),      # Mukla — golden
]
MECHS = [
    ("EX1_556", 2, 3, {}, False),                    # Harvest Golem
    ("BOT_312", 2, 6, {"taunt": True}, False),       # Replicating Menace
    ("GVG_106", 1, 5, {"poisonous": True}, False),   # Junkbot
    ("GVG_113", 6, 9, {}, True),                     # Foe Reaper 4000
]


def _minions(spec) -> tuple[Minion, ...]:
    return tuple(
        Minion(
            entity_id=100 + i, card_id=card_id, position=i + 1,
            attack=attack, health=health, golden=golden, tier=3, **flags,
        )
        for i, (card_id, attack, health, flags, golden) in enumerate(spec)
    )


def _board(hero_card_id: str, health: int, tier: int, spec, player_id: int = 1) -> PlayerBoard:
    return PlayerBoard(
        player_id=player_id, bg_player_id=player_id, hero_card_id=hero_card_id,
        hero_entity_id=player_id, health=health, armor=0, tier=tier,
        minions=_minions(spec),
    )


def _standings() -> ev.Standings:
    return ev.Standings(places=tuple(
        ev.Standing(place=place, player_id=place, hero_card_id=card_id,
                    health=hp, dead=dead, you=you)
        for place, card_id, hp, dead, you in HEROES
    ))


async def run(overlay, interval: float = 4.0) -> None:
    """Loop the three game phases forever, `interval` seconds apart."""
    you = _board("TB_BaconShop_HERO_43", 18, 3, MECHS, player_id=1)
    enemy = _board("TB_BaconShop_HERO_94", 27, 5, DEMONS, player_id=2)
    combat = BoardSnapshot(turn=7, friendly=you, opponent=enemy)
    odds = SimResult(
        won_percent=63, tied_percent=9, lost_percent=28,
        avg_damage_won=14, avg_damage_lost=9,
    )
    buffs = ev.Buffs(entries=(("Blood Gem", 2, 2), ("Elemental", 4, 3)),
                     spells=("BG28_800", "BG28_168"))  # Careful Investment, Shiny Ring

    # (event, prediction, dwell) — dwell is how long the resulting state stays
    # on screen. Each phase gets a full interval so the flip between Hero
    # Select, Combat Forecast and Recruit Phase is actually watchable; the
    # bookkeeping events in between pass straight through.
    script = [
        (ev.GameStart(), None, interval),
        (_standings(), None, 0.0),
        (ev.HeroPicked(card_id="TB_BaconShop_HERO_43"), None, 0.0),
        (ev.TurnChange(turn=7), None, 0.0),
        (buffs, None, 0.0),
        (ev.CombatStart(snapshot=combat), odds, interval),
        (ev.NextOpponent(player_id=4), None, 0.0),
        (ev.CombatEnd(snapshot=combat), None, interval),
    ]

    for event, prediction, dwell in itertools.cycle(script):
        overlay.on_event(event, prediction)
        await asyncio.sleep(dwell or 0.05)
