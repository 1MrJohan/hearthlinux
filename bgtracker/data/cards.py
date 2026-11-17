"""Card name/metadata lookup backed by HearthstoneJSON, cached on disk."""

from __future__ import annotations

import json
import logging
import time
import urllib.request

from bgtracker.config import CACHE_DIR

log = logging.getLogger(__name__)

CARDS_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"
CACHE_FILE = CACHE_DIR / "cards.json"
CACHE_MAX_AGE = 7 * 24 * 3600

_by_id: dict[str, dict] = {}
_pool_spells: set[str] | None = None
_trinkets: set[str] | None = None


def load(refresh: bool = False) -> bool:
    """Load the card DB (downloading/refreshing the cache as needed)."""
    global _by_id
    stale = True
    if CACHE_FILE.is_file():
        stale = time.time() - CACHE_FILE.stat().st_mtime > CACHE_MAX_AGE
    if refresh or stale or not CACHE_FILE.is_file():
        try:
            log.info("downloading card database…")
            req = urllib.request.Request(
                CARDS_URL, headers={"User-Agent": "hs-bg-tracker/0.1 (personal Linux BG tracker)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_bytes(data)
        except Exception as exc:
            log.warning("card DB download failed (%s); using cache if present", exc)
    if not CACHE_FILE.is_file():
        return False
    _by_id = {c["id"]: c for c in json.loads(CACHE_FILE.read_bytes())}
    global _pool_spells, _trinkets
    _pool_spells = None
    _trinkets = None
    return True


def name(card_id: str | None) -> str:
    if not card_id:
        return "?"
    card = _by_id.get(card_id)
    return card["name"] if card else card_id


def get(card_id: str) -> dict | None:
    return _by_id.get(card_id)


# Battlegrounds tribes, card-DB name -> the name the game uses. The display name
# lowercased is also the tribe's icon file name (data/art.py).
TRIBE_NAMES = {
    "ABERRATION": "Aberration",
    "BEAST": "Beast",
    "DEMON": "Demon",
    "DRAGON": "Dragon",
    "ELEMENTAL": "Elemental",
    "MECHANICAL": "Mech",
    "MURLOC": "Murloc",
    "NAGA": "Naga",
    "PIRATE": "Pirate",
    "QUILBOAR": "Quilboar",
    "UNDEAD": "Undead",
}


def is_quest(card_id: str | None) -> bool:
    """Whether a card is a quest objective rather than an effect.

    Backs up the log's own `QUEST` tag, and is the only signal for combat
    rows stored while quests were still filed under `secrets`. The id shape
    is checked too so `--no-names` and a stale cache still answer.
    """
    if not card_id:
        return False
    card = _by_id.get(card_id)
    if card is not None and "QUEST" in (card.get("mechanics") or ()):
        return True
    return "_Quest_" in card_id


def tribe_name(tribe: str) -> str:
    return TRIBE_NAMES.get(tribe, tribe.title())


def tribes(card_id: str | None) -> tuple[str, ...]:
    """A card's tribes as the DB names them (`MECHANICAL`, `QUILBOAR`, `ALL`, …).

    Empty for an unknown card or an unloaded DB. `races` is the full list for
    dual-tribe minions; older entries carry only the singular `race`.
    """
    card = _by_id.get(card_id) if card_id else None
    if not card:
        return ()
    return tuple(card.get("races") or ([card["race"]] if card.get("race") else ()))


def pool_spell_ids() -> set[str]:
    """Card ids of Battlegrounds pool spells (Easterly Winds & co).

    Classifies by the stable `isBattlegroundsPoolSpell` flag rather than the
    entity CARDTYPE, which the game morphs at runtime. Recomputed until the DB
    is actually loaded so a pre-load call can't cache an empty set.
    """
    global _pool_spells
    if _pool_spells is None and _by_id:
        _pool_spells = {cid for cid, c in _by_id.items() if c.get("isBattlegroundsPoolSpell")}
    return _pool_spells or set()


def trinket_ids() -> set[str]:
    """Card ids of Battlegrounds trinkets.

    Classified from the card DB rather than the entity's runtime CARDTYPE,
    which the game morphs (the same reason pool spells are matched by flag).
    Whether a trinket is *equipped* is a zone question, not a type one: the
    equipped ones sit in PLAY, while offers and rejected discoveries pile up
    in SETASIDE and REMOVEDFROMGAME.
    """
    global _trinkets
    if _trinkets is None and _by_id:
        _trinkets = {
            cid for cid, c in _by_id.items()
            if c.get("type") == "BATTLEGROUND_TRINKET"
        }
    return _trinkets or set()
