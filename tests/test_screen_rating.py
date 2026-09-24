"""Reading the rating off the post-game banner, and what the pipeline does with it.

The fixtures are crops of real 2560x1440 captures — a 3rd-place banner
(Rating 6143, +44), a 2nd-place one (6286, +72) whose curtain edge and
sparkles sit beside the change, and a 6th-place loss (6217, -69) — served through a fake window at their
original offset so the geometry is exercised exactly as it runs live. Tests that need real OCR
self-skip without tesseract, as the sim round-trip does without node.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from pathlib import Path

import pytest

from bgtracker.app import Pipeline
from bgtracker.config import Config
from bgtracker.history.db import HistoryDB
from bgtracker.parse import events as ev
from bgtracker.screen import rating as sr
from bgtracker.screen.capture import Pixels
from bgtracker.screen.rating import Reading, RatingReader

SCREENS = Path(__file__).parent / "fixtures" / "screens"
FIXTURE = SCREENS / "rating-win-6143-plus44.png"
SECOND = SCREENS / "rating-2nd-6286-plus72.png"
LOSS = SCREENS / "rating-6th-6217-minus69.png"
# Where the crop sat in the 2560x1440 window it was taken from.
WINDOW = (2560, 1440)
ORIGIN = (848, 864)

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed"
)


class FixtureWindow:
    """A GameWindow serving grabs out of the fixture crop."""

    def __init__(self, recolour=None, fixture=FIXTURE):
        image = pytest.importorskip("PIL.Image").open(fixture).convert("RGB")
        if recolour is not None:
            pixels = getattr(image, "get_flattened_data", image.getdata)()
            image.putdata([recolour(*p) for p in pixels])
        self.image = image

    def size(self):
        return WINDOW

    def grab(self, x, y, w, h):
        ox, oy = ORIGIN
        crop = self.image.crop((x - ox, y - oy, x - ox + w, y - oy + h))
        r, g, b = crop.split()
        return Pixels(w, h, r.tobytes(), g.tobytes(), b.tobytes())

    def close(self):
        pass


def _yellow_to_red(r, g, b):
    if r > 180 and g > 150 and b < 110:
        return (220, 40, 40)
    return (r, g, b)


# -- geometry ------------------------------------------------------------

def test_regions_land_inside_the_fixture_crop():
    """If a region spilled past the crop, the fake window would pad it and the
    test would pass on pixels that never existed."""
    w, h = pytest.importorskip("PIL.Image").open(FIXTURE).size
    for box in (sr.LABEL, sr.BAND):
        x, y, rw, rh = sr.region(box, *WINDOW)
        assert ORIGIN[0] <= x and x + rw <= ORIGIN[0] + w
        assert ORIGIN[1] <= y and y + rh <= ORIGIN[1] + h


def test_regions_scale_with_height_about_the_centre():
    x, y, w, h = sr.region(sr.LABEL, 1920, 1080)
    assert (x + w / 2) == pytest.approx(960, abs=1)
    assert h == pytest.approx((0.695 - 0.655) * 1080, abs=1)


def test_regions_are_clamped_to_the_window():
    x, y, w, h = sr.region((-5.0, -1.0, 5.0, 2.0), 800, 600)
    assert (x, y, w, h) == (0, 0, 800, 600)


# -- colour and glyphs ----------------------------------------------------

def _locate(window):
    return sr.locate(sr.split_ink(window.grab(*sr.region(sr.BAND, *WINDOW))))


def test_a_gain_is_yellow():
    assert _locate(FixtureWindow()).sign == 1


def test_a_loss_is_red():
    assert _locate(FixtureWindow(fixture=LOSS)).sign == -1


def test_the_curtain_edge_beside_the_change_is_not_a_glyph():
    """2-5px-wide runs of gold 29px past "+72" read as "+72   - 55" when they
    were fed to OCR with it. Only glyph-wide runs are kept."""
    ink = sr.split_ink(FixtureWindow(fixture=SECOND).grab(*sr.region(sr.BAND, *WINDOW)))
    narrow = [r for r in ink.gain_runs if r.width < 6]
    assert narrow, "the fixture no longer contains the noise this test is about"
    banner = sr.locate(ink)
    kept = {x for x in range(ink.width) if 0 in banner.change[-ink.height * ink.width:][x::ink.width]}
    assert all(not (r.start <= x <= r.end) for r in narrow for x in kept)


def _ink(white=(), gain=(), loss=(), width=500, height=108):
    runs = lambda spec: tuple(sr.Run(a, b, 30, 82) for a, b in spec)
    blank = b"\xff" * (width * height)
    return sr.Ink(width, height, blank, blank, blank, runs(white), runs(gain), runs(loss))


RATING = ((116, 154), (170, 190), (208, 243), (252, 287))


def test_both_colours_or_neither_is_no_banner():
    assert sr.locate(_ink(RATING)) is None
    assert sr.locate(_ink(RATING, gain=((320, 359),), loss=((320, 359),))) is None


def test_a_change_too_far_from_the_rating_is_not_its_change():
    assert sr.locate(_ink(RATING, gain=((400, 430),))) is None   # 112px > 1.5 digits
    assert sr.locate(_ink(RATING, gain=((320, 359), (375, 403)))).sign == 1
    # A sign with no digits after it is not a change.
    assert sr.locate(_ink(RATING, gain=((320, 359),))) is None


def test_specks_go_and_glyphs_stay():
    """A sparkle sharing a glyph's columns survives the column filter; only its
    size gives it away."""
    w, h = 10, 10
    mask = bytearray(b"\xff" * (w * h))
    for y in range(1, 9):                       # a 2x8 stroke: 16px
        mask[y * w + 2] = mask[y * w + 3] = 0
    mask[0 * w + 7] = mask[1 * w + 7] = 0      # a 2px speck
    out = sr._despeckle(bytes(mask), w, h, min_area=5)
    assert out[5 * w + 2] == 0 and out[0 * w + 7] == 255


def test_no_white_is_no_banner():
    assert sr.locate(_ink(gain=((320, 359),))) is None


# -- acceptance ----------------------------------------------------------

@pytest.mark.parametrize("label,rating,digits,sign,expected", [
    ("Rating", "6143", "44", 1, Reading(6143, 44)),
    ("Rating\n", " 6217 ", "69", -1, Reading(6217, -69)),
    ("Rating", "10230", "7", 1, Reading(10230, 7)),
    # Another screen entirely.
    ("Ratings", "6143", "44", 1, None),
    ("", "6143", "44", 1, None),
    # A sign in the digits means the sign glyph was not where it should be.
    ("Rating", "6143", "+44", 1, None),
    ("Rating", "6143", "-44", -1, None),
    ("Rating", "6143", "", 1, None),
    # Digits that are not a rating, or not a change.
    ("Rating", "61", "44", 1, None),
    ("Rating", "614E", "44", 1, None),
    ("Rating", "99999", "44", 1, None),
    ("Rating", "6143", "1044", 1, None),
])
def test_accept(label, rating, digits, sign, expected):
    assert sr.accept(label, rating, digits, sign) == expected


# -- the whole read, with real OCR ---------------------------------------

@needs_tesseract
def test_the_fixture_reads_as_6143_plus_44():
    reader = RatingReader(window=FixtureWindow())
    assert asyncio.run(reader.read_once()) == Reading(6143, 44)


@needs_tesseract
def test_the_second_place_banner_reads_as_6286_plus_72():
    reader = RatingReader(window=FixtureWindow(fixture=SECOND))
    assert asyncio.run(reader.read_once()) == Reading(6286, 72)


@needs_tesseract
def test_the_loss_banner_reads_as_6217_minus_69():
    """psm 13 alone read this "-69" as a bare "-" while the sign sat in the
    image beside the digits."""
    reader = RatingReader(window=FixtureWindow(fixture=LOSS))
    assert asyncio.run(reader.read_once()) == Reading(6217, -69)


def test_red_ink_in_the_shape_of_a_plus_is_refused():
    """The recoloured fixture still draws a "+". Colour and shape disagree, and
    a disagreement is never resolved by picking one."""
    assert _locate(FixtureWindow(recolour=_yellow_to_red)) is None


def test_a_minus_is_flat_and_a_plus_is_square():
    loss = sr.split_ink(FixtureWindow(fixture=LOSS).grab(*sr.region(sr.BAND, *WINDOW)))
    gain = sr.split_ink(FixtureWindow().grab(*sr.region(sr.BAND, *WINDOW)))
    minus, plus = loss.loss_runs[0], gain.gain_runs[0]
    assert minus.height / minus.width <= sr.MINUS_MAX_ASPECT
    assert plus.height / plus.width >= sr.PLUS_MIN_ASPECT


class NoWindow:
    def size(self):
        return None

    def close(self):
        pass


def test_no_game_window_is_no_reading():
    assert asyncio.run(RatingReader(window=NoWindow()).read_once()) is None


# -- polling -------------------------------------------------------------

class Scripted(RatingReader):
    """Returns a scripted sequence of reads, then keeps returning the last."""

    def __init__(self, reads):
        super().__init__(window=NoWindow())
        self.reads = list(reads)
        self.calls = 0

    async def read_once(self):
        self.calls += 1
        if len(self.reads) > 1:
            return self.reads.pop(0)
        return self.reads[0]


def test_a_counting_number_is_waited_out():
    reader = Scripted([Reading(6120, 44), Reading(6135, 44), Reading(6143, 44)])
    got = asyncio.run(reader.watch(seconds=5, interval=0))
    assert got == Reading(6143, 44)


def test_one_good_read_is_not_enough():
    reader = Scripted([None, Reading(6143, 44), None])
    assert asyncio.run(reader.watch(seconds=0.05, interval=0.001)) is None


def test_a_raising_read_is_a_miss_not_a_crash():
    class Raising(Scripted):
        async def read_once(self):
            raise OSError("X server went away")
    assert asyncio.run(Raising([None]).watch(seconds=0.02, interval=0.001)) is None


# -- storage -------------------------------------------------------------

def test_an_old_ratings_table_gains_the_screen_columns(tmp_path):
    """The author's real database predates these columns, and CREATE TABLE IF
    NOT EXISTS leaves it alone — only the ALTER reaches it."""
    path = tmp_path / "history.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE ratings (id INTEGER PRIMARY KEY,"
                " recorded_at TEXT NOT NULL, rating INTEGER NOT NULL)")
    old.execute("INSERT INTO ratings (recorded_at, rating) VALUES ('2026-09-01T00:00:00+00:00', 6000)")
    old.commit()
    old.close()

    db = HistoryDB(path)
    columns = {row[1] for row in db.conn.execute("PRAGMA table_info(ratings)")}
    assert {"game_id", "delta", "source"} <= columns
    assert db.conn.execute("SELECT rating, source FROM ratings").fetchall() == [(6000, None)]
    db.close()


def test_one_screen_reading_per_game(tmp_path):
    db = HistoryDB(tmp_path / "history.db")
    game = db.start_game("g1")
    assert db.record_rating(6143, game_id=game, delta=44, source="screen") is True
    assert db.record_rating(6143, game_id=game, delta=44, source="screen") is False
    # Manual readings carry no game and are never deduplicated.
    assert db.record_rating(6150) is True
    assert db.record_rating(6150) is True
    assert db.conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 3
    db.close()


# -- the pipeline --------------------------------------------------------

def _pipeline(tmp_path, reader, **cfg):
    db = HistoryDB(tmp_path / "history.db")
    pipe = Pipeline(sim=None, db=db, rating_reader=reader, cfg=Config(**cfg))
    seen = []
    pipe.listeners.append(
        lambda e, _p: seen.append(e) if isinstance(e, (ev.RatingRead, ev.RatingMissed)) else None
    )
    return pipe, db, seen


async def _settle(pipe):
    if pipe._rating_task is not None:
        await asyncio.gather(pipe._rating_task, return_exceptions=True)


def test_a_live_game_end_records_the_screen_reading(tmp_path):
    reader = Scripted([Reading(6143, 44)])

    async def run():
        pipe, db, seen = _pipeline(tmp_path, reader)
        await pipe.handle([ev.GameStart(log_id="g1"), ev.GameEnd(placement=3)])
        await _settle(pipe)
        return db, seen

    db, seen = asyncio.run(run())
    assert seen == [ev.RatingRead(rating=6143, delta=44)]
    row = db.conn.execute("SELECT rating, delta, source, game_id FROM ratings").fetchone()
    game = db.conn.execute("SELECT id FROM games WHERE log_id = 'g1'").fetchone()[0]
    assert row == (6143, 44, "screen", game)


def test_catch_up_never_reads_the_screen(tmp_path):
    """A game that ended while the tracker was not watching: the screen now
    shows something else, possibly another game's banner."""
    reader = Scripted([Reading(6143, 44)])

    async def run():
        pipe, db, seen = _pipeline(tmp_path, reader)
        await pipe.handle([ev.GameStart(log_id="g1"), ev.GameEnd(placement=3)],
                          historical=True)
        return pipe, db, seen

    pipe, db, seen = asyncio.run(run())
    assert pipe._rating_task is None and reader.calls == 0
    assert seen == [ev.RatingMissed(reason="caught up from the log")]
    assert db.conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 0


def test_the_setting_off_misses_at_once(tmp_path):
    reader = Scripted([Reading(6143, 44)])

    async def run():
        pipe, _, seen = _pipeline(tmp_path, reader, mmr_screen_read=False)
        await pipe.handle([ev.GameStart(log_id="g1"), ev.GameEnd(placement=3)])
        return pipe, seen

    pipe, seen = asyncio.run(run())
    assert pipe._rating_task is None and reader.calls == 0
    assert seen == [ev.RatingMissed(reason="screen reading is off")]


def test_no_reader_misses_at_once(tmp_path):
    async def run():
        pipe, _, seen = _pipeline(tmp_path, None)
        await pipe.handle([ev.GameStart(log_id="g1"), ev.GameEnd(placement=3)])
        return seen

    assert asyncio.run(run()) == [ev.RatingMissed(reason="screen reader unavailable")]


def test_a_read_that_gives_up_is_a_miss(tmp_path):
    class Never(Scripted):
        async def watch(self, seconds=0, interval=0):
            return None

    async def run():
        pipe, db, seen = _pipeline(tmp_path, Never([None]))
        await pipe.handle([ev.GameStart(log_id="g1"), ev.GameEnd(placement=3)])
        await _settle(pipe)
        return db, seen

    db, seen = asyncio.run(run())
    assert seen == [ev.RatingMissed(reason="not seen on screen")]
    assert db.conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 0


def test_the_next_game_cancels_a_read_in_progress(tmp_path):
    class Slow(Scripted):
        async def watch(self, seconds=0, interval=0):
            await asyncio.sleep(10)
            return Reading(6143, 44)

    async def run():
        pipe, db, seen = _pipeline(tmp_path, Slow([None]))
        await pipe.handle([ev.GameStart(log_id="g1"), ev.GameEnd(placement=3)])
        task = pipe._rating_task
        await pipe.handle([ev.GameStart(log_id="g2")])
        await asyncio.gather(task, return_exceptions=True)
        return task, db, seen

    task, db, seen = asyncio.run(run())
    assert task.cancelled()
    assert seen == []
    assert db.conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 0
