"""Read the new rating and its change off the post-game banner.

Every Battlegrounds game ends on a banner showing the placement and, under a
pinkish `Rating` label, the new rating in white and the change beside it —
yellow on a gain, red on a loss. No log carries either number. See
docs/superpowers/specs/2026-09-23-mmr-screen-read-design.md.

Two rules shape the reader, and both are about never recording a wrong number:

- **The sign never goes through OCR.** Next to digits it breaks tesseract in
  both directions: line mode read `+44` as `144` and `+72` as `+12`, raw-line
  mode read `-69` as a bare `-`. The sign is the change's colour, confirmed by
  the shape of its first glyph — a `+` is square, a `-` is flat — and only the
  digits after it are OCR'd.
- **Agreement, twice over.** The digits are read in two segmentation modes
  that must agree, and two consecutive captures must agree. The banner
  animates, and a read taken mid-count is a wrong number that looks right.

The rating and the change are read out of one wide band split by colour
rather than out of two fixed boxes, so a five-digit rating pushing the change
rightwards moves no boundary. The band also catches whatever the banner's
frame puts beside the numbers — on the 2nd-place banner, the curtain's gold
edge and animated sparkles sit 29px past the change — so only *glyphs* are
kept: runs of ink columns at least `MIN_GLYPH_WIDTH` digit-heights wide,
chained from the rating outwards. The noise measured so far is 2-5px wide;
the narrowest glyph, a `1`, is 21px.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from bgtracker.config import CACHE_DIR
from bgtracker.history.db import RATING_MAX, RATING_MIN
from bgtracker.sim.cpu import efficiency_cpus

from .capture import GameWindow, Pixels

log = logging.getLogger(__name__)

# How long after GameEnd to keep looking. GameState ends the game when the
# final fight resolves, but the player may watch that fight for up to ~48s
# before the banner appears, and it then stays until they click past it.
SCREEN_READ_SECONDS = 90.0
POLL_SECONDS = 0.4
OCR_TIMEOUT = 5.0

# Frames of banners that were located but never read, for diagnosing a layout
# the reader does not know yet. Disposable, like the rest of the cache.
MISS_DIR = CACHE_DIR / "banners"
KEEP_MISSES = 10
# How often, while the pixel check finds nothing, the label is read on its own
# in case a banner is up that the pixel check does not recognise.
PROBE_SECONDS = 2.0

# Regions as (x0, y0, x1, y1): x from the window's horizontal centre and y from
# its top, both in units of window HEIGHT — the banner is centred and Unity
# scales UI by height. Measured on a 2560x1440 capture; other aspect ratios are
# a best guess from that.
LABEL = (-0.0625, 0.655, 0.0625, 0.695)
BAND = (-0.14, 0.685, 0.22, 0.760)
# The Battlegrounds lobby's rating panel, where a player who clicks past the
# banner mid-count lands. Measured on the same 1440p capture basis.
LOBBY_LABEL = (0.391, 0.150, 0.507, 0.188)
LOBBY_NUMBER = (0.36, 0.190, 0.54, 0.245)

# Glyph geometry, in units of the rating's digit height (51px at 1440p).
MIN_GLYPH_WIDTH = 0.15   # narrower runs are frame edges and sparkles
MAX_GLYPH_GAP = 1.0      # widest gap inside a number is 17px
MAX_REACH = 1.5          # rating to change is 31px
# Height over width of the change's first glyph: a "+" measures 1.02, a "-"
# 0.56. A sign glyph that does not match its colour means the chain is not
# what it looks like.
PLUS_MIN_ASPECT = 0.8
MINUS_MAX_ASPECT = 0.7
# Blobs smaller than this share of a digit-height square are sparkles, even
# when they share a glyph's columns (one above the "2" of "+72" read as a
# trailing "1"). A "-" is ~0.08 of one; the specks seen are under 0.01.
MIN_BLOB = 0.02
# Before the digit height is known, the rating's own runs are found with a
# floor in units of band height (a `1` is 21px of a 108px band).
MIN_RATING_GLYPH = 0.1


@dataclass(frozen=True)
class Reading:
    rating: int
    # The banner's change for this game; None for a lobby snapshot, which
    # says what the rating is but not what this game did.
    delta: int | None = None


@dataclass(frozen=True)
class Run:
    """Contiguous columns holding one colour's ink, and the rows it spans."""

    start: int
    end: int      # inclusive
    top: int
    bottom: int

    @property
    def width(self) -> int:
        return self.end - self.start + 1

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1


@dataclass(frozen=True)
class Ink:
    """A band split by colour: 8-bit masks, 0 where the ink is, and the runs."""

    width: int
    height: int
    white: bytes
    gain: bytes
    loss: bytes
    white_runs: tuple[Run, ...]
    gain_runs: tuple[Run, ...]
    loss_runs: tuple[Run, ...]


@dataclass(frozen=True)
class Banner:
    """The rating and the change located in a band, ready for OCR."""

    rating: bytes   # PGM
    change: bytes   # PGM of the change's digits, without its sign glyph
    sign: int


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


def _runs(top: list[int], bottom: list[int]) -> tuple[Run, ...]:
    runs, start = [], None
    for x in range(len(top) + 1):
        inked = x < len(top) and top[x] >= 0
        if inked and start is None:
            start = x
        elif not inked and start is not None:
            runs.append(Run(start, x - 1,
                            min(top[start:x]), max(bottom[start:x])))
            start = None
    return tuple(runs)


def split_ink(px: Pixels) -> Ink:
    """One pass over the band, sorting each pixel into white, yellow or red."""
    w, h = px.width, px.height
    masks = [bytearray(b"\xff" * (w * h)) for _ in range(3)]
    tops = [[-1] * w for _ in range(3)]
    bottoms = [[-1] * w for _ in range(3)]
    for y in range(h):
        row = y * w
        for x, (r, g, b) in enumerate(zip(px.r[row:row + w], px.g[row:row + w],
                                          px.b[row:row + w])):
            if r > 200 and g > 200 and b > 200:
                k = 0
            elif r > 180 and g > 150 and b < 110:
                k = 1
            elif r > 170 and g < 90 and b < 90:
                k = 2
            else:
                continue
            masks[k][row + x] = 0
            if tops[k][x] < 0:
                tops[k][x] = y
            bottoms[k][x] = y
    return Ink(w, h, *(bytes(m) for m in masks),
               *(_runs(tops[k], bottoms[k]) for k in range(3)))


def _chain(runs: tuple[Run, ...], after: int, reach: float, gap: float,
           min_width: float) -> list[Run]:
    """Glyph runs starting within `reach` of column `after`, each within `gap`
    of the last. Narrow runs are skipped, not chained through."""
    glyphs = [r for r in runs if r.width >= min_width and r.start > after]
    if not glyphs or glyphs[0].start - after > reach:
        return []
    chain = [glyphs[0]]
    for run in glyphs[1:]:
        if run.start - chain[-1].end > gap:
            break
        chain.append(run)
    return chain


def _keep(mask: bytes, width: int, runs: list[Run]) -> bytes:
    """`mask` with everything outside `runs`' columns blanked."""
    out = bytearray(b"\xff" * len(mask))
    for row in range(0, len(mask), width):
        for run in runs:
            out[row + run.start:row + run.end + 1] = mask[row + run.start:row + run.end + 1]
    return bytes(out)


def _despeckle(mask: bytes, width: int, height: int, min_area: float) -> bytes:
    """`mask` without its 4-connected ink blobs smaller than `min_area`."""
    out = bytearray(mask)
    seen = bytearray(len(mask))
    for origin in range(len(mask)):
        if mask[origin] or seen[origin]:
            continue
        blob, stack = [], [origin]
        seen[origin] = 1
        while stack:
            i = stack.pop()
            blob.append(i)
            x = i % width
            for j in (i - width, i + width, i - 1 if x else -1, i + 1 if x + 1 < width else -1):
                if 0 <= j < len(mask) and not mask[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        if len(blob) < min_area:
            for i in blob:
                out[i] = 255
    return bytes(out)


def _pgm(mask: bytes, width: int, height: int) -> bytes:
    return b"P5 %d %d 255\n" % (width, height) + mask


def locate(ink: Ink) -> Banner | None:
    """The rating's glyphs and exactly one colour of change beside them."""
    floor = MIN_RATING_GLYPH * ink.height
    rating = _chain(ink.white_runs, -1, ink.width, ink.width, floor)
    if not rating:
        return None
    digit = max(r.height for r in rating)
    rating = _chain(ink.white_runs, -1, ink.width,
                    MAX_GLYPH_GAP * digit, MIN_GLYPH_WIDTH * digit)
    end = rating[-1].end
    changes = {
        sign: _chain(runs, end, MAX_REACH * digit, MAX_GLYPH_GAP * digit,
                     MIN_GLYPH_WIDTH * digit)
        for sign, runs in ((1, ink.gain_runs), (-1, ink.loss_runs))
    }
    found = [sign for sign, chain in changes.items() if chain]
    if len(found) != 1:
        return None
    sign = found[0]
    glyph, *digits = changes[sign]
    aspect = glyph.height / glyph.width
    if not digits or (aspect < PLUS_MIN_ASPECT if sign > 0 else aspect > MINUS_MAX_ASPECT):
        return None
    change_mask = ink.gain if sign > 0 else ink.loss
    blob = MIN_BLOB * digit * digit

    def clean(mask: bytes, runs: list[Run]) -> bytes:
        kept = _despeckle(_keep(mask, ink.width, runs), ink.width, ink.height, blob)
        return _pgm(kept, ink.width, ink.height)

    return Banner(rating=clean(ink.white, rating),
                  change=clean(change_mask, digits), sign=sign)


def lobby_digits(ink: Ink) -> bytes | None:
    """The lobby panel's rating as a PGM of its white glyphs, or None."""
    rating = _chain(ink.white_runs, -1, ink.width, ink.width, MIN_RATING_GLYPH * ink.height)
    if not rating:
        return None
    digit = max(r.height for r in rating)
    rating = _chain(ink.white_runs, -1, ink.width,
                    MAX_GLYPH_GAP * digit, MIN_GLYPH_WIDTH * digit)
    if not 3 <= len(rating) <= 5:
        return None
    kept = _despeckle(_keep(ink.white, ink.width, rating), ink.width, ink.height,
                      MIN_BLOB * digit * digit)
    return _pgm(kept, ink.width, ink.height)


def lobby_value(label: str, digits: str, digits_word: str) -> int | None:
    # Letters only: the label crop's edge catches the panel frame, which OCR
    # reads as a stray ")" (the 6069 capture read "Rating)").
    if re.sub(r"[^a-z]", "", label.lower()) != "rating" or digits != digits_word:
        return None
    digits = digits.strip()
    if not re.fullmatch(r"\d{3,5}", digits):
        return None
    value = int(digits)
    return value if RATING_MIN <= value <= RATING_MAX else None


def accept(label: str, rating: str, digits: str, sign: int) -> Reading | None:
    """The OCR'd strings as a reading, or None if any of them is doubtful.
    `digits` is the change without its sign, which `sign` carries."""
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
    digits = digits.strip()
    if not re.fullmatch(r"\d{1,3}", digits):
        return None
    return Reading(rating=value, delta=sign * int(digits))


def _deferential_prefix() -> list[str]:
    """Run tesseract where the simulator runs: on the efficiency cores at idle
    priority. The window now spans the final fight, which the player is still
    watching, and a hitch is one preempted frame. Through util-linux rather
    than a preexec_fn, which keeps the fork hazard sim/cpu.py documents out of
    this path entirely; missing tools just mean normal priority."""
    prefix: list[str] = []
    cpus = efficiency_cpus()
    if cpus and shutil.which("taskset"):
        prefix += ["taskset", "-c", ",".join(str(c) for c in sorted(cpus))]
    if shutil.which("chrt"):
        prefix += ["chrt", "--idle", "0"]
    return prefix


async def ocr(image: bytes, psm: int, whitelist: str | None = None,
              tesseract: str = "tesseract", prefix: list[str] | None = None) -> str:
    args = [*(prefix or ()), tesseract, "stdin", "stdout", "--psm", str(psm)]
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


@dataclass(frozen=True)
class Attempt:
    """One poll: whether a banner was on screen, what it read as, and the
    frame, kept so a banner that never reads can be looked at afterwards."""

    located: bool
    reading: Reading | None = None
    band: bytes | None = None    # PPM
    label: bytes | None = None   # PPM
    # The label read "Rating": this frame really is the banner. A board's
    # white attack numbers beside red health gems pass the pixel check too.
    labelled: bool = False


def _save_miss(attempt: Attempt, directory: Path, keep: int = KEEP_MISSES) -> None:
    """Write the frame of a banner that never read; keep the newest `keep`."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (directory / f"{stamp}-band.ppm").write_bytes(attempt.band)
        (directory / f"{stamp}-label.ppm").write_bytes(attempt.label)
        for old in sorted(directory.glob("*-band.ppm"))[:-keep]:
            old.unlink(missing_ok=True)
            old.with_name(old.name.replace("-band", "-label")).unlink(missing_ok=True)
    except OSError as exc:
        log.debug("could not keep the unread banner: %r", exc)


class RatingReader:
    def __init__(self, window: GameWindow | None = None, tesseract: str = "tesseract",
                 prefix: list[str] | None = None, misses: Path | None = MISS_DIR):
        self.window = window if window is not None else GameWindow()
        self.tesseract = tesseract
        self.prefix = _deferential_prefix() if prefix is None else prefix
        self.misses = misses

    async def _ocr(self, image: bytes, psm: int, whitelist: str | None = None) -> str:
        return await ocr(image, psm, whitelist, self.tesseract, self.prefix)

    async def attempt(self) -> Attempt:
        size = self.window.size()
        if size is None:
            return Attempt(located=False)
        band_px = self.window.grab(*region(BAND, *size))
        if band_px is None:
            return Attempt(located=False)
        # Cheap pixel checks first: most polls land on a screen with no banner,
        # and those should cost an X round trip, not a tesseract run.
        banner = locate(split_ink(band_px))
        if banner is None:
            return Attempt(located=False)
        label_px = self.window.grab(*region(LABEL, *size))
        if label_px is None:
            return Attempt(located=False)
        # One round, label and digits together: the banner is on screen for
        # as long as the player takes to click past it. Raw line and single
        # word segment differently, so one misreading a glyph the other reads
        # right is a disagreement.
        label, rating, rating_word, digits, digits_word = await asyncio.gather(
            self._ocr(label_px.ppm(), 7),
            *(self._ocr(image, psm, "0123456789")
              for image in (banner.rating, banner.change) for psm in (13, 8))
        )
        reading = None
        if rating == rating_word and digits == digits_word:
            reading = accept(label, rating, digits, banner.sign)
        if reading is None:
            log.debug("banner not accepted: label=%r rating=%r/%r digits=%r/%r sign=%d",
                      label, rating, rating_word, digits, digits_word, banner.sign)
        return Attempt(located=True, reading=reading,
                       band=band_px.ppm(), label=label_px.ppm(),
                       labelled=label.strip().lower() == "rating")

    async def probe(self) -> Attempt | None:
        """The frame, if the label reads "Rating" whatever the pixels say."""
        size = self.window.size()
        if size is None:
            return None
        band_px = self.window.grab(*region(BAND, *size))
        label_px = self.window.grab(*region(LABEL, *size))
        if band_px is None or label_px is None:
            return None
        if (await self._ocr(label_px.ppm(), 7)).strip().lower() != "rating":
            return None
        return Attempt(located=False, band=band_px.ppm(), label=label_px.ppm(),
                       labelled=True)

    async def read_lobby(self) -> int | None:
        """The rating on the Battlegrounds lobby's panel, or None."""
        size = self.window.size()
        if size is None:
            return None
        number_px = self.window.grab(*region(LOBBY_NUMBER, *size))
        if number_px is None:
            return None
        digits = lobby_digits(split_ink(number_px))
        if digits is None:
            return None
        label_px = self.window.grab(*region(LOBBY_LABEL, *size))
        if label_px is None:
            return None
        label, number, number_word = await asyncio.gather(
            self._ocr(label_px.ppm(), 7),
            self._ocr(digits, 13, "0123456789"),
            self._ocr(digits, 8, "0123456789"),
        )
        return lobby_value(label, number, number_word)

    async def read_once(self) -> Reading | None:
        return (await self.attempt()).reading

    async def watch(self, seconds: float = SCREEN_READ_SECONDS,
                    interval: float = POLL_SECONDS,
                    previous_rating: int | None = None) -> Reading | None:
        """Poll until a read joins `previous_rating` or two consecutive reads
        agree, or `seconds` run out. While no banner is up, the lobby's panel
        is tried too, and two agreeing lobby reads give a snapshot with no
        delta — the player clicked the banner away before it could be read.

        `rating − delta` is the rating before the game. When it equals the
        last stored one, a single read is accepted: a count caught mid-way or
        a misread digit would have to be wrong in exactly the way that still
        subtracts to it."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + seconds
        last: Reading | None = None
        seen_at: float | None = None
        miss: Attempt | None = None
        probed = started
        lobby_last: int | None = None
        while loop.time() < deadline:
            try:
                attempt = await self.attempt()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("screen read failed: %r", exc)
                attempt = Attempt(located=False)
            reading = attempt.reading
            if attempt.labelled and seen_at is None:
                seen_at = loop.time()
                log.info("end-screen banner confirmed %.1fs after the game ended",
                         seen_at - started)
            joins = (reading is not None and previous_rating is not None
                     and reading.rating - reading.delta == previous_rating)
            if joins or (reading is not None and reading == last):
                log.info("rating %d (%+d) read %.2fs after the banner appeared%s",
                         reading.rating, reading.delta, loop.time() - seen_at,
                         "" if joins else ", by agreement")
                return reading
            if attempt.located and reading is None and attempt.labelled:
                miss = attempt
            elif not attempt.located and loop.time() - probed >= PROBE_SECONDS:
                probed = loop.time()
                unrecognised = await self.probe()
                if unrecognised is not None:
                    if miss is None or not miss.located:
                        miss = unrecognised
                    log.info("a banner is up that the pixel check does not recognise")
                else:
                    lobby = await self.read_lobby()
                    # Equal to the last reading is either a game that moved
                    # nothing or a panel not yet refreshed; nothing says which.
                    if lobby is not None and lobby != previous_rating:
                        if lobby == lobby_last:
                            log.info("rating %d read off the lobby; the banner was "
                                     "clicked away before it could be read", lobby)
                            return Reading(rating=lobby)
                        lobby_last = lobby
            last = reading
            # A banner is up for as long as the player takes to click past
            # it, so while one is located the next read starts at once.
            await asyncio.sleep(0 if attempt.located else interval)
        if miss is not None:
            log.info("end-screen banner seen but never read; frame kept in %s",
                     self.misses)
            if self.misses is not None:
                _save_miss(miss, self.misses)
        return None

    def close(self) -> None:
        self.window.close()
