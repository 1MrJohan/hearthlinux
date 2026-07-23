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
    global _pool_spells
    _pool_spells = None
    return True


def name(card_id: str | None) -> str:
    if not card_id:
        return "?"
    card = _by_id.get(card_id)
    return card["name"] if card else card_id


def get(card_id: str) -> dict | None:
    return _by_id.get(card_id)


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
