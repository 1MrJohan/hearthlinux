# Placement Revision Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. Do the tasks in order; each ends with the whole suite green and one commit.

**Goal:** When the local hero's leaderboard place changes after `GameEnd` (within ~54ms of `STATE = COMPLETE` in every observed case), emit `PlacementRevised`. The change then reaches the stored placement, the overlay's Game Over subtitle, and the console.

**Architecture:** The exporter already follows the named hero (`BGExporter.named_hero()`) and emits `GameEnd` once (`_maybe_emit_end`). It gains `_announced`, the last placement it put in an event, and a revision branch in the `PLAYER_LEADERBOARD_PLACE` handler. `Pipeline` keeps the ended game's id until the next `GameStart` and writes revisions through a new `HistoryDB.set_placement`. The overlay and `headless.print_event` each gain one match arm.

**Tech Stack:** Python 3.14 (system interpreter via `.venv --system-site-packages`), hslog, pytest.

**Spec:** [docs/superpowers/specs/2026-09-23-placement-after-complete-design.md](../specs/2026-09-23-placement-after-complete-design.md)

## Global Constraints

- Run `.venv/bin/python -m pytest tests/` (the whole suite) after every task.
- `GameEnd`'s trigger does not change. No delay, no batching, no timer. The concede path (`_conceded`) and `finalize()` keep their current behaviour.
- `PlacementRevised`, `set_placement`, `_announced` and `_ended_game_id` are the names later tasks depend on.
- Comments say why, never what. Commit messages explain the failure and name what the change was verified against.
- `history.db` is written only through `HistoryDB`. The data repair in Task 5 is gated on the user, after a backup.

---

## Task 1: The exporter announces a revision

**Files:**
- Modify: `bgtracker/parse/events.py` (new event, add to the `Event` union)
- Modify: `bgtracker/parse/exporter.py` (`__init__`, the `PLAYER_LEADERBOARD_PLACE` branch, `_maybe_emit_end`, `finalize`)
- Modify: `tests/test_parser_events.py`

- [ ] **Step 1: Write the failing tests** at the end of the early-concede block in `tests/test_parser_events.py`. They reuse `_mid_game()` and `_ends()`:

```python
def _revisions(lines):
    return [e for e in feed_all(lines)[1] if isinstance(e, ev.PlacementRevised)]


def _completed_at(place: int) -> LogBuilder:
    b = _mid_game()
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", place)
    b.tag_change("GameEntity", "STATE", "COMPLETE")
    return b


def test_a_place_that_settles_after_complete_is_revised():
    """Game 408: 7 at COMPLETE, 6 24ms later, and the banner said 6th."""
    b = _completed_at(7)
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 6)
    assert _ends(b.lines) == [ev.GameEnd(placement=7)]
    assert _revisions(b.lines) == [ev.PlacementRevised(placement=6)]


def test_the_same_place_again_is_not_a_revision():
    b = _completed_at(7)
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 7)
    assert _revisions(b.lines) == []


def test_every_change_is_revised_and_the_last_one_stands():
    b = _completed_at(7)
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 6)
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 5)
    assert _revisions(b.lines) == [ev.PlacementRevised(placement=6),
                                   ev.PlacementRevised(placement=5)]


def test_another_heros_place_after_complete_is_not_ours():
    b = _completed_at(7)
    b.tag_change(5, "PLAYER_LEADERBOARD_PLACE", 1)
    assert _revisions(b.lines) == []


def test_the_next_game_ends_the_watch():
    b = _completed_at(7)
    b.add("CREATE_GAME")
    b.tag_change(4, "PLAYER_LEADERBOARD_PLACE", 6)
    assert _revisions(b.lines) == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_parser_events.py -k "revis or watch or same_place or another_hero" -q`
Expected: `AttributeError: module 'bgtracker.parse.events' has no attribute 'PlacementRevised'`.

- [ ] **Step 3: Add the event** in `bgtracker/parse/events.py`, directly after `GameEnd`, and add `| PlacementRevised` after `| GameEnd` in the `Event` union:

```python
@dataclass(frozen=True)
class PlacementRevised:
    """The local hero's place changed after GameEnd announced it. In game 408
    it landed 24ms after STATE=COMPLETE and matched the banner; the last
    revision before the next game stands."""

    placement: int
```

- [ ] **Step 4: Track what was announced.** In `BGExporter.__init__`, next to `self._conceded = False`:

```python
        # The placement last put in a GameEnd or PlacementRevised; the named
        # hero's place can still move after COMPLETE.
        self._announced: int | None = None
```

In `_maybe_emit_end`, set it where the event is emitted:

```python
        if placement:
            self._end_emitted = True
            self._announced = placement
            self._emit(ev.GameEnd(placement=placement))
```

In `finalize`, set `self._announced = placement` before its emit, for symmetry. Nothing revises after finalize, because the exporter is discarded.

- [ ] **Step 5: Emit revisions.** In the `PLAYER_LEADERBOARD_PLACE` branch, after the existing `if self._ended: self._maybe_emit_end()`:

```python
            if (self._end_emitted and value and value != self._announced
                    and entity is self.named_hero()):
                self._announced = value
                self._emit(ev.PlacementRevised(placement=value))
```

The `_end_emitted` guard must come after `_maybe_emit_end()`. The tag that lets `GameEnd` out is not also announced as its own revision, because `_announced` already equals it.

- [ ] **Step 6: Run the parser tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_parser_events.py -q`, then `.venv/bin/python -m pytest tests/`
Expected: all pass.

- [ ] **Step 7: Commit.** Subject: "Announce a placement that settles after COMPLETE". The body cites game 408 (7 at COMPLETE, 6 at +24ms, banner 6th).

---

## Task 2: The revision reaches the database

**Files:**
- Modify: `bgtracker/history/db.py` (new `set_placement`)
- Modify: `bgtracker/app.py` (`Pipeline.__init__`, the `GameStart`, `GameEnd` and new `PlacementRevised` arms)
- Modify: `tests/test_history.py`

- [ ] **Step 1: Write the failing tests** in `tests/test_history.py`:

```python
def test_a_revised_placement_is_what_the_history_keeps(tmp_path):
    from bgtracker.parse import events as ev
    db = HistoryDB(tmp_path / "history.db")
    pipeline = Pipeline(sim=None, db=db)
    asyncio.run(pipeline.handle([
        ev.GameStart(log_id="g1"),
        ev.GameEnd(placement=7),
        ev.PlacementRevised(placement=6),
    ]))
    assert db.conn.execute("SELECT placement FROM games").fetchone() == (6,)


def test_a_revision_after_the_next_game_starts_touches_nothing(tmp_path):
    from bgtracker.parse import events as ev
    db = HistoryDB(tmp_path / "history.db")
    pipeline = Pipeline(sim=None, db=db)
    asyncio.run(pipeline.handle([
        ev.GameStart(log_id="g1"), ev.GameEnd(placement=7),
        ev.GameStart(log_id="g2"), ev.PlacementRevised(placement=6),
    ]))
    rows = db.conn.execute("SELECT log_id, placement FROM games ORDER BY id").fetchall()
    assert rows == [("g1", 7), ("g2", None)]
```

- [ ] **Step 2: Run them and watch them fail** (`AttributeError` on `set_placement`, or the placement staying 7).

- [ ] **Step 3: Add `HistoryDB.set_placement`** after `end_game`:

```python
    def set_placement(self, game_id: int, placement: int) -> None:
        # Not end_game: that also rewrites final_turn and stamps ended_at, and
        # a revision is only ever about the place.
        self.conn.execute("UPDATE games SET placement = ? WHERE id = ?", (placement, game_id))
        self.conn.commit()
```

- [ ] **Step 4: Wire the pipeline.** In `Pipeline.__init__`: `self._ended_game_id: int | None = None`. In the `GameStart` arm, set `self._ended_game_id = None` before `start_game`. In the `GameEnd` arm, set `self._ended_game_id = game_id` (the id captured before `_game_id` is cleared). Add an arm:

```python
                case ev.PlacementRevised(placement=place):
                    if self.db and self._ended_game_id:
                        self.db.set_placement(self._ended_game_id, place)
```

- [ ] **Step 5: Run the whole suite.** Expected: all pass.

- [ ] **Step 6: Commit.** Subject: "Store a placement revised after the game ended".

---

## Task 3: The overlay and console show it

**Files:**
- Modify: `bgtracker/overlay/app.py` (`on_event`)
- Modify: `bgtracker/headless.py` (`print_event`)
- Modify: `tests/test_overlay_events.py`

- [ ] **Step 1: Write the failing tests:**

```python
def test_a_revised_placement_updates_game_over():
    app, win = _app()
    app.on_event(ev.GameEnd(placement=7), None)
    app.on_event(ev.PlacementRevised(placement=6), None)
    assert win.last("set_phase") == ("Game Over", "finished #6")
    assert app.state.status == "Finished #6"


def test_a_revision_leaves_a_rating_on_the_status_line():
    app, _ = _app()
    app.on_event(ev.GameEnd(placement=7), None)
    app.on_event(ev.RatingRead(rating=6217, delta=-69), None)
    app.on_event(ev.PlacementRevised(placement=6), None)
    assert app.state.status == "MMR 6217 (-69)"
```

- [ ] **Step 2: Run them and watch them fail.**

- [ ] **Step 3: Add the overlay arm** after the `GameEnd` arm:

```python
            case ev.PlacementRevised(placement=p):
                st.phase = ("Game Over", f"finished #{p}")
                if st.status.startswith("Finished #"):
                    st.status = f"Finished #{p}"
```

- [ ] **Step 4: Add the console arm** after `GameEnd` in `headless.print_event`:

```python
        case ev.PlacementRevised(placement=p):
            print(f"  placement settled: {p}")
```

- [ ] **Step 5: Run the whole suite.** Expected: all pass.

- [ ] **Step 6: Commit.** Subject: "Show a settled placement on Game Over".

---

## Task 4: Verify against every retained log, and update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the "Placement tags land a few packets *after* the `STATE = COMPLETE`" bullet)

- [ ] **Step 1: Replay every retained session log** through `LiveGameProcessor` in 5000-line batches, printing each game's `log_id`, its `GameEnd` placement and every `PlacementRevised`.
  Expected, from the spec:
  - 9/22 15:54 → 6, revised 5
  - 9/22 17:03 → 4, revised 3
  - 9/22 17:27 → 6, revised 5
  - 9/23 16:51 (408) → 7, revised 6
  - every other game: its current placement and no revision
  - 409 still ends as 8 at its concede

  Any other revision is a finding: stop and report it rather than adjusting a test to match.

- [ ] **Step 2: Rewrite the CLAUDE.md bullet** to say that the named hero always has a live place, so `GameEnd` fires at `COMPLETE`, and that a change within ~54ms afterwards arrives as `PlacementRevised` (5 of 20 games, banner-confirmed on 408). Link the spec.

- [ ] **Step 3: Run the whole suite and commit.** Subject: "Note that placements can settle after COMPLETE". The body carries the replay counts.

---

## Task 5: Repair the three 9/22 games (human-gated)

Only after the user says yes.

- [ ] **Step 1: Back up:** `sqlite3 ~/.local/share/hs-bg-tracker/history.db ".backup '<path>.bak-<utc stamp>-pre-settle-fix'"`.
- [ ] **Step 2: Replay with recording:** `.venv/bin/python -m bgtracker --replay "<Logs>/Hearthstone_2026_09_22_15_20_12/Power.log" --record`. `start_game` resumes each row by `log_id`, `record_combat` and `end_game` are COALESCE upserts, and the revision lands through `set_placement`.
- [ ] **Step 3: Check** that games `15:54:30.807728`, `17:03:31.721665` and `17:27:30.369818` read 5, 3 and 5. Check that no other row in that session changed placement, and that the game and combat counts match the backup.

---

## Merge

Merge `placement-revision` into master with `--no-ff` and a summary subject. Then restart the tracker with `--replace`.
