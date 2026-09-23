"""Read the new rating and its change off the post-game banner.

Every Battlegrounds game ends on a banner showing the placement and, under a
pinkish `Rating` label, the new rating in white and the change beside it —
yellow on a gain, red on a loss. No log carries either number. See
docs/superpowers/specs/2026-09-23-mmr-screen-read-design.md.

Two rules shape the reader, and both are about never recording a wrong number:

- **The sign comes from colour, never from OCR.** tesseract's line mode reads
  `+44` as `144`, which is a plausible delta. The raw-line mode reads the `+`
  correctly, and the reader then requires the OCR'd sign to *agree* with the
  colour — a check that fails closed rather than a guess that happens to work.
- **Two consecutive captures must agree.** The banner animates, and a read
  taken mid-count is a wrong number that looks right.

The rating and the change are read out of one wide band split by colour
rather than out of two fixed boxes, so a five-digit rating pushing the change
rightwards moves no boundary.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass

from bgtracker.history.db import RATING_MAX, RATING_MIN

from .capture import GameWindow, Pixels

log = logging.getLogger(__name__)

# How long after GameEnd to keep looking. The banner is up a few seconds after
# the game ends and stays until the player clicks past it; clicking through
# fast costs the automatic read, and the manual nudge takes over.
SCREEN_READ_SECONDS = 30.0
POLL_SECONDS = 0.4
OCR_TIMEOUT = 5.0

# Regions as (x0, y0, x1, y1): x from the window's horizontal centre and y from
# its top, both in units of window HEIGHT — the banner is centred and Unity
# scales UI by height. Measured on a 2560x1440 capture; other aspect ratios are
# a best guess from that.
LABEL = (-0.0625, 0.655, 0.0625, 0.695)
BAND = (-0.14, 0.685, 0.22, 0.760)

# Share of the band a colour must cover to count as present. The fixture's
# rating covers 6.2% and its change 2.8%.
MIN_INK = 0.005


@dataclass(frozen=True)
class Reading:
    rating: int
    delta: int


@dataclass(frozen=True)
class Ink:
    """A band split by colour. Masks are 8-bit greyscale, 0 where the ink is."""

    width: int
    height: int
    white: bytes
    gain: bytes
    loss: bytes
    white_count: int
    gain_count: int
    loss_count: int

    def present(self, count: int) -> bool:
        return count >= MIN_INK * self.width * self.height

    def pgm(self, mask: bytes) -> bytes:
        return b"P5 %d %d 255\n" % (self.width, self.height) + mask


def region(box: tuple[float, float, float, float], width: int, height: int
           ) -> tuple[int, int, int, int]:
    """`box` in window pixels as (x, y, w, h), clamped to the window."""
    x0, y0, x1, y1 = box
    centre = width / 2
    left = max(0, round(centre + x0 * height))
    right = min(width, round(centre + x1 * height))
    top = max(0, round(y0 * height))
    bottom = min(height, round(y1 * height))
    return left, top, right - left, bottom - top


def split_ink(px: Pixels) -> Ink:
    """One pass over the band, sorting each pixel into white, yellow or red."""
    n = px.width * px.height
    white, gain, loss = bytearray(b"\xff" * n), bytearray(b"\xff" * n), bytearray(b"\xff" * n)
    counts = [0, 0, 0]
    for i, (r, g, b) in enumerate(zip(px.r, px.g, px.b)):
        if r > 200 and g > 200 and b > 200:
            white[i] = 0
            counts[0] += 1
        elif r > 180 and g > 150 and b < 110:
            gain[i] = 0
            counts[1] += 1
        elif r > 170 and g < 90 and b < 90:
            loss[i] = 0
            counts[2] += 1
    return Ink(px.width, px.height, bytes(white), bytes(gain), bytes(loss), *counts)


def sign_of(ink: Ink) -> int | None:
    """+1 for a yellow change, -1 for red, None unless exactly one is there."""
    gain, loss = ink.present(ink.gain_count), ink.present(ink.loss_count)
    if gain == loss:
        return None
    return 1 if gain else -1


def accept(label: str, rating: str, delta: str, sign: int) -> Reading | None:
    """The OCR'd strings as a reading, or None if any of them is doubtful."""
    # What keeps the reader off every other screen, including whatever the
    # player has already clicked through to.
    if label.strip().lower() != "rating":
        return None
    rating = rating.strip()
    if not re.fullmatch(r"\d{3,5}", rating):
        return None
    value = int(rating)
    if not RATING_MIN <= value <= RATING_MAX:
        return None
    change = re.fullmatch(r"([+-])(\d{1,3})", delta.strip())
    if change is None or (change[1] == "+") != (sign > 0):
        return None
    return Reading(rating=value, delta=sign * int(change[2]))


async def ocr(image: bytes, psm: int, whitelist: str | None = None,
              tesseract: str = "tesseract") -> str:
    args = [tesseract, "stdin", "stdout", "--psm", str(psm)]
    if whitelist:
        args += ["-c", f"tessedit_char_whitelist={whitelist}"]
    # One thread: tesseract's OpenMP pool would otherwise wake every core for a
    # 500-pixel-wide crop.
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(image), OCR_TIMEOUT)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        raise
    return out.decode(errors="replace").strip()


def unavailable() -> str | None:
    """Why the reader cannot run on this machine, or None if it can."""
    if shutil.which("tesseract") is None:
        return "tesseract not installed"
    try:
        __import__("Xlib.display")
    except Exception:
        return "python-xlib missing"
    return None


class RatingReader:
    def __init__(self, window: GameWindow | None = None, tesseract: str = "tesseract"):
        self.window = window if window is not None else GameWindow()
        self.tesseract = tesseract

    async def read_once(self) -> Reading | None:
        size = self.window.size()
        if size is None:
            return None
        band_px = self.window.grab(*region(BAND, *size))
        if band_px is None:
            return None
        ink = split_ink(band_px)
        sign = sign_of(ink)
        # Cheap pixel checks first: most polls land on a screen with no banner,
        # and those should cost an X round trip, not three tesseract runs.
        if sign is None or not ink.present(ink.white_count):
            return None
        label_px = self.window.grab(*region(LABEL, *size))
        if label_px is None:
            return None
        label, rating, delta = await asyncio.gather(
            ocr(label_px.ppm(), 7, tesseract=self.tesseract),
            ocr(ink.pgm(ink.white), 13, "0123456789", self.tesseract),
            ocr(ink.pgm(ink.gain if sign > 0 else ink.loss), 13, "+-0123456789",
                self.tesseract),
        )
        reading = accept(label, rating, delta, sign)
        if reading is None:
            log.debug("banner not accepted: label=%r rating=%r delta=%r sign=%d",
                      label, rating, delta, sign)
        return reading

    async def watch(self, seconds: float = SCREEN_READ_SECONDS,
                    interval: float = POLL_SECONDS) -> Reading | None:
        """Poll until two consecutive reads agree, or `seconds` run out."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        previous: Reading | None = None
        while loop.time() < deadline:
            try:
                reading = await self.read_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("screen read failed: %r", exc)
                reading = None
            if reading is not None and reading == previous:
                return reading
            previous = reading
            await asyncio.sleep(interval)
        return None

    def close(self) -> None:
        self.window.close()
