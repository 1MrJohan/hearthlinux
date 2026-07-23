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
TILE_DIR = ART_DIR / "tiles"
CARD_DIR = ART_DIR / "cards"
TILE_URL = "https://art.hearthstonejson.com/v1/tiles/{card_id}.png"
# Full rendered card (frame + art + name + printed stats), 256px wide.
RENDER_URL = "https://art.hearthstonejson.com/v1/render/latest/enUS/256x/{card_id}.png"
# Art crop (illustration only, no frame) — much broader coverage; used as a
# fallback for the newest sets / goldens that have no rendered card yet.
ARTCROP_URL = "https://art.hearthstonejson.com/v1/256x/{card_id}.jpg"
_UA = {"User-Agent": "hs-bg-tracker/0.1 (personal Linux BG tracker)"}
_failed: set[str] = set()


def _fetch(url: str, dest: Path) -> Path | None:
    return _fetch_first([url], dest)


def _fetch_first(urls: list[str], dest: Path) -> Path | None:
    """Download the first URL that succeeds into dest (cached by dest name)."""
    if dest.is_file():
        return dest
    if dest.name in _failed:
        return None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)  # format detected by content, not extension
            return dest
        except Exception as exc:
            log.debug("art fetch miss %s: %s", url, exc)
    _failed.add(dest.name)
    return None


# -- thin deck-tile crop (legacy) --------------------------------------
def tile_path(card_id: str) -> Path:
    # Historic layout stored tiles at ART_DIR/{id}.png; keep reading those,
    # but write new ones under tiles/ to keep the cache tidy.
    legacy = ART_DIR / f"{card_id}.png"
    return legacy if legacy.is_file() else TILE_DIR / f"{card_id}.png"


def cached_tile(card_id: str) -> Path | None:
    p = tile_path(card_id)
    return p if p.is_file() else None


async def fetch_tile(card_id: str) -> Path | None:
    if not card_id:
        return None
    cached = cached_tile(card_id)
    if cached:
        return cached
    dest = TILE_DIR / f"{card_id}.png"
    url = TILE_URL.format(card_id=card_id)
    return await asyncio.get_running_loop().run_in_executor(None, _fetch, url, dest)


# -- full rendered card ------------------------------------------------
def card_path(card_id: str) -> Path:
    return CARD_DIR / f"{card_id}.png"


def cached_card(card_id: str) -> Path | None:
    p = card_path(card_id)
    return p if p.is_file() else None


def _card_urls(card_id: str) -> list[str]:
    """Preferred → fallback art sources so every card gets an image.

    Full render first; for goldens (…_G) try the base card's render (older
    goldens); then the art crop, which covers the newest sets/goldens that
    have no rendered card yet.
    """
    urls = [RENDER_URL.format(card_id=card_id)]
    base = card_id[:-2] if card_id.endswith("_G") else None
    if base:
        urls.append(RENDER_URL.format(card_id=base))
    urls.append(ARTCROP_URL.format(card_id=card_id))
    if base:
        urls.append(ARTCROP_URL.format(card_id=base))
    return urls


async def fetch_card(card_id: str) -> Path | None:
    """Fetch (or return cached) a card image, trying several sources."""
    if not card_id:
        return None
    cached = cached_card(card_id)
    if cached:
        return cached
    dest = card_path(card_id)
    urls = _card_urls(card_id)
    return await asyncio.get_running_loop().run_in_executor(None, _fetch_first, urls, dest)
