"""Card tile art from HearthstoneJSON, cached on disk.

Tiles are the small wide crops (deck-tile style) used for board rows.
Downloads happen in an executor so the UI loop never blocks; a miss just
renders as text until the art arrives.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.request
from pathlib import Path

from bgtracker.config import CACHE_DIR

log = logging.getLogger(__name__)

ART_DIR = CACHE_DIR / "art"
TILE_URL = "https://art.hearthstonejson.com/v1/tiles/{card_id}.png"
_UA = {"User-Agent": "hs-bg-tracker/0.1 (personal Linux BG tracker)"}
_failed: set[str] = set()


def tile_path(card_id: str) -> Path:
    return ART_DIR / f"{card_id}.png"


def cached_tile(card_id: str) -> Path | None:
    p = tile_path(card_id)
    return p if p.is_file() else None


def _download(card_id: str) -> Path | None:
    p = tile_path(card_id)
    if p.is_file():
        return p
    if card_id in _failed:
        return None
    try:
        req = urllib.request.Request(TILE_URL.format(card_id=card_id), headers=_UA)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        ART_DIR.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p
    except Exception as exc:
        _failed.add(card_id)
        log.debug("tile fetch failed for %s: %s", card_id, exc)
        return None


async def fetch_tile(card_id: str) -> Path | None:
    """Fetch (or return cached) tile without blocking the event loop."""
    if not card_id:
        return None
    cached = cached_tile(card_id)
    if cached:
        return cached
    return await asyncio.get_running_loop().run_in_executor(None, _download, card_id)
