"""Pure sidecar compatibility policy, exercised through Node without workers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

ROOT = Path(__file__).parent.parent


def _check(
    cards: dict,
    mappings: list[str],
    ids: list[str],
    combat_ids: list[str] | None = None,
    secrets: list[str] | None = None,
) -> dict:
    script = """
import { checkBattleCompatibility } from './sidecar/compatibility.mjs';
const payload = JSON.parse(process.argv[1]);
const allCards = { getCard: (id) => payload.cards[id] ?? {} };
const implemented = new Set(payload.mappings);
const minions = payload.ids.map((cardId) => ({ cardId, enchantments: [] }));
const input = {
  playerBoard: {
    player: {
      secrets: payload.secrets.map((cardId, entityId) => ({ cardId, entityId })),
    },
    board: minions,
  },
  opponentBoard: { player: {}, board: [] },
  trackerCombatCardIds: payload.combatIds,
};
console.log(JSON.stringify(checkBattleCompatibility(input, allCards, implemented)));
"""
    payload = json.dumps({
        "cards": cards,
        "mappings": mappings,
        "ids": ids,
        "combatIds": combat_ids or [],
        "secrets": secrets or [],
    })
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script, payload],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(done.stdout)


def test_mapped_effect_without_card_data_fails_closed():
    assert _check({}, ["NEW_DEATHRATTLE"], ["NEW_DEATHRATTLE"]) == {
        "missingData": ["NEW_DEATHRATTLE"],
        "unsupported": [],
    }


def test_unmapped_cards_are_not_guessed_unsupported():
    cards = {
        "COMBAT": {
            "id": "COMBAT",
            "mechanics": ["TRIGGER_VISUAL"],
            "text": "Whenever a friendly Beast attacks, give it +2/+2.",
        },
        "RECRUIT": {
            "id": "RECRUIT",
            "mechanics": ["TRIGGER_VISUAL"],
            "text": "After you buy a minion, give it +1/+1.",
        },
    }
    # Firestone also has legacy switch-based implementations, so the absence
    # of a mapping is not evidence that a described effect is unsupported.
    assert _check(cards, [], ["COMBAT", "RECRUIT"]) == {
        "missingData": [],
        "unsupported": [],
    }


def test_tracker_marks_new_combat_effect_missing_from_sidecar_data():
    assert _check({}, [], ["BG36_202"], combat_ids=["BG36_202"]) == {
        "missingData": [],
        "unsupported": ["BG36_202"],
    }


def test_secret_effect_is_included_in_compatibility_check():
    assert _check({}, [], [], combat_ids=["UNKNOWN_SECRET"], secrets=["UNKNOWN_SECRET"]) == {
        "missingData": [],
        "unsupported": ["UNKNOWN_SECRET"],
    }


def test_mapped_combat_effect_with_data_is_allowed():
    cards = {
        "SUPPORTED": {
            "id": "SUPPORTED",
            "mechanics": ["DEATHRATTLE"],
            "text": "Deathrattle: Summon a 2/2 Beast.",
        },
    }
    assert _check(cards, ["SUPPORTED"], ["SUPPORTED"]) == {
        "missingData": [],
        "unsupported": [],
    }


def test_pinned_package_catalog_includes_current_bg36_combat_effects():
    script = """
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { implementedCardIds } from './sidecar/compatibility.mjs';
const require = createRequire(join(process.cwd(), 'sidecar', 'package.json'));
const dist = join(dirname(require.resolve(
  '@firestone-hs/simulate-bgs-battle/package.json',
)), 'dist');
const ids = implementedCardIds(dist);
console.log(JSON.stringify({
  legacySwitch: ids.has('BG29_611'),
  packTactics: ids.has('TB_Bacon_Secrets_15'),
  tastyLobster: ids.has('BG36_202'),
  cageGnawer: ids.has('BG36_211_G'),
}));
"""
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(done.stdout) == {
        "legacySwitch": True,
        "packTactics": True,
        "tastyLobster": True,
        "cageGnawer": True,
    }


def test_card_cache_must_be_newer_than_behavior_package():
    script = """
import { cacheIsFresh } from './sidecar/compatibility.mjs';
const day = 24 * 60 * 60 * 1000;
const now = 10 * day;
console.log(JSON.stringify({
  currentPair: cacheIsFresh({mtimeMs: 9 * day}, {mtimeMs: 8 * day}, now, 7 * day),
  packageNewer: cacheIsFresh({mtimeMs: 8 * day}, {mtimeMs: 9 * day}, now, 7 * day),
  cacheExpired: cacheIsFresh({mtimeMs: 2 * day}, {mtimeMs: 1 * day}, now, 7 * day),
}));
"""
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(done.stdout) == {
        "currentPair": True,
        "packageNewer": False,
        "cacheExpired": False,
    }
