# Reading the rating off the lobby when the banner is clicked away

## Problem

Game 415 (4th, 6019 → 6069) was not read. The frame kept by the miss
diagnostics shows why: the banner's rating was still counting up (6055 of
6069), and the change is drawn only once the count lands. The player clicked
past before that, so there never was a complete banner to read. No banner
reader can beat that click. Refusing the half-counted number was correct.

The screen the player lands on next does carry the number. The Battlegrounds
lobby shows a `Rating` panel on the right with the current rating, 6069. It
was captured 40s after the game ended, and tesseract read it exactly:
the label read `Rating` and the number read `6069` in raw-line, word and
line modes. It is kept as `tests/fixtures/screens/lobby-6069.png` (origin
1712,144 in a 2560x1440 window).

## Decision

**When the banner has not been read, the reader also looks for the lobby
panel during the same window after `GameEnd`, and records what it shows as a
snapshot: a rating with no per-game change.**

- **A snapshot, not a delta.** The lobby says what the rating *is*, not what
  this game did. Stored with `game_id`, `source = 'lobby'` and `delta` NULL,
  it is read like a typed reading. The history gives the game a change only
  when it is the only game since the previous reading, and `mmr_breaks`
  ignores it. 415's +50 is exactly that case. Computing `rating − previous`
  here would be the interpolation CLAUDE.md forbids whenever a game went
  untracked.
- **Only after a banner miss.** A banner read carries the per-game change and
  wins. The lobby is tried only on polls where no banner is located, at the
  probe cadence (every 2s), so the final fight is not spent OCR-ing the board.
- **Same checks as the banner, minus the sign.** A pixel gate first: white
  glyph runs in the number region, chained as on the banner. Then the label
  must read `Rating`, and the digits must agree in raw-line and word modes and
  fall within `RATING_MIN..RATING_MAX`. Two consecutive lobby reads must agree.
- **Not the previous rating.** A lobby value equal to the last stored reading
  is refused. It is either a game that moved nothing, which is rare, or a
  panel not yet refreshed, and the two cannot be told apart. The nudge covers
  it.
- **Regions** are height-relative offsets from the window centre, like the
  banner's, measured on the 1440p capture. Label: x 0.391…0.507, y
  0.150…0.188. Number: x 0.36…0.54, y 0.190…0.245. The lobby layout at other
  aspect ratios is as untested as the banner's.

`Reading.delta` and `RatingRead.delta` become optional. The overlay and the
console print `MMR 6069` when there is no change to show.

## Not covered

- A player who goes somewhere other than the Battlegrounds lobby, or stays in
  it for less than a poll, gets the nudge as today.
- The lobby panel on game launch, which would catch games the tracker never
  saw, is a different trigger. The reader added here could serve it.

## Verification

- `pytest tests/`: the lobby fixture reads 6069; a banner read still wins over
  the lobby; a lobby read needs two agreeing polls; a lobby value equal to the
  previous rating is refused; a lobby reading is stored with no delta and the
  history derives the game's change only when it is isolated.
- Live: a game clicked past mid-count records the lobby's rating.
