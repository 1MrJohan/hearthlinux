"""Card and hero art from HearthstoneJSON, cached on disk.

Two kinds are available:

* ``crop``   — the square illustration with no card frame. This is what the
  overlay's circular minion portraits and hero orbs use, and it has the
  broadest CDN coverage (new sets and goldens land here first).
* ``render`` — the full rendered card (frame + art + name + printed stats).

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
CROP_DIR = ART_DIR / "crops"
CARD_DIR = ART_DIR / "cards"

# Full rendered card (frame + art + name + printed stats), 256px wide.
RENDER_URL = "https://art.hearthstonejson.com/v1/render/latest/enUS/256x/{card_id}.png"
# Art crop (illustration only, no frame), 256x256.
ARTCROP_URL = "https://art.hearthstonejson.com/v1/256x/{card_id}.jpg"

_KINDS = {
    "crop": (CROP_DIR, ".jpg"),
    "render": (CARD_DIR, ".png"),
}
_UA = {"User-Agent": "hs-bg-tracker/0.1 (personal Linux BG tracker)"}
# Keyed by full destination path, not bare filename: crops and renders share
# card-id filenames, and a failed crop must not poison the render lookup.
_failed: set[Path] = set()


def _fetch_first(urls: list[str], dest: Path) -> Path | None:
    """Download the first URL that succeeds into dest (cached by dest path)."""
    if dest.is_file():
        return dest
    if dest in _failed:
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
    _failed.add(dest)
    return None


def art_path(card_id: str, kind: str = "crop") -> Path:
    directory, suffix = _KINDS[kind]
    return directory / f"{card_id}{suffix}"


def cached_art(card_id: str, kind: str = "crop") -> Path | None:
    if not card_id:
        return None
    path = art_path(card_id, kind)
    return path if path.is_file() else None


def _urls(card_id: str, kind: str) -> list[str]:
    """Preferred -> fallback art sources so every card gets an image.

    Goldens (`…_G`) frequently have no art of their own; the base card's is
    visually identical bar the animation, so it stands in.
    """
    base = card_id[:-2] if card_id.endswith("_G") else None
    ids = [card_id] + ([base] if base else [])
    if kind == "render":
        # Full render first, then fall back to the wider-coverage crop.
        return [RENDER_URL.format(card_id=i) for i in ids] + [
            ARTCROP_URL.format(card_id=i) for i in ids
        ]
    return [ARTCROP_URL.format(card_id=i) for i in ids]


async def fetch_art(card_id: str, kind: str = "crop") -> Path | None:
    """Fetch (or return cached) card art, trying several sources."""
    if not card_id:
        return None
    cached = cached_art(card_id, kind)
    if cached:
        return cached
    dest = art_path(card_id, kind)
    urls = _urls(card_id, kind)
    return await asyncio.get_running_loop().run_in_executor(
        None, _fetch_first, urls, dest
    )
