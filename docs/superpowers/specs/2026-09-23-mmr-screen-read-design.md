# Reading the rating off the post-game screen

## Problem

Every MMR number the tracker has was typed by hand. On 2026-09-23 there were
84 readings in the history database against 405 games, so almost no game has a
delta: `review.game_list` gives one only when a game is the *only* game between
two consecutive readings, and the rest honestly show nothing. The end-of-game
nudge (`2026-08-29-mmr-prompt-design.md`) made typing easier. It did not make it
happen every game.

The rating is not in any log. Every file in a session directory was searched
(`Power.log`, `Hearthstone.log`, `GameNetLogger.log`, `Gameplay.log` and the
rest): the only rating-shaped hits are `PLAYER_LEADERBOARD_PLACE`, which is the
in-lobby position. HDT and Firestone get the rating by reading game memory,
which this project does not do. Blizzard's public leaderboard API lists only
the top of each region, far above the author's ~6100.

The rating *is* on screen. Every Battlegrounds game ends on a banner that shows
the placement and, underneath, the new rating and the change:

```
      3rd Place!
        Rating
     6143  +44
```

The change is yellow on a gain and red on a loss (the author confirmed the
red). The label is a pinkish purple and the rating is white.

## What makes this possible

**The game window can be captured with the X11 connection we already have.**
Hearthstone runs on XWayland, so `Window.get_image` on its X window returns the
game's own pixels with no Wayland screenshot portal and no permission prompt.
`overlay/hover.py` already opens a `python-xlib` display to poll the pointer.
Checked on the author's machine: the window is found by `WM_NAME ==
"Hearthstone"` (its `WM_CLASS` is `steam_app_2559601395`, which depends on the
launcher and is useless for matching). A full 2560×1440 capture works both
mid-game and on the end screen.

The overlay cannot show up in that capture: it is a Wayland layer-shell surface,
not part of the X window.

**tesseract reads the banner cleanly once each colour is split out.** Tested
against the capture saved as `tests/fixtures/screens/rating-win-6143-plus44.png`:

| Input | tesseract | Result |
|---|---|---|
| label crop, raw colour, `--psm 7` | `Rating` | ✔ |
| rating band, white pixels only, `--psm 13` | `6143` | ✔ |
| same band, yellow pixels only, `--psm 13` | `+44` | ✔ |
| same band, red pixels only | *(empty)* | ✔ no false sign on a gain |

The first attempt used `--psm 7` for the change and got `144`: that mode reads
the `+` as a `1`. `--psm 13` (raw line) fixes it. That one fact also sets the
design: **the sign comes from colour, never from OCR**. The yellow-or-red test
decides the sign, and OCR only has to read digits.

Each tesseract call took ~70–100ms. The masked band is written as a PGM, which
tesseract reads directly, so the reader needs no Pillow and no upscaling.

The reading checks itself: the previous stored reading was 6099, and
6099 + 44 = 6143.

## Decision

After a **live** `GameEnd`, the pipeline starts a short-lived task that
captures the banner, OCRs it, and records the rating together with the
screen's own delta and the game it closes. If the read fails for any reason,
the existing manual nudge appears exactly as it does today.

### Geometry: one band, split by colour

Regions are expressed as offsets from the window's **horizontal centre** in
**units of window height**, because the banner is centred and Unity scales UI
by height. Measured at 2560×1440:

| Region | x (from centre, ×H) | y (×H) |
|---|---|---|
| label | −0.0625 … +0.0625 | 0.655 … 0.695 |
| rating + change band | −0.14 … +0.22 | 0.685 … 0.760 |

A fixed box each for the rating and the change would break when a rating
reaches five digits and pushes the change right. One wide band with the
colours separated has no boundary to get wrong: white pixels are the rating
wherever they sit, yellow or red pixels are the change.

Only the two regions are fetched (`get_image(x, y, w, h)`), not the whole
window: about 56k pixels, so the X round trip does not stall the shared GTK +
asyncio loop.

Colour thresholds, from the fixture:

- white: r, g, b all > 200
- yellow: r > 180, g > 150, b < 110
- red: r > 170, g < 90, b < 90. **The thresholds are untested on a real loss:
  there is no loss capture yet.**

### Acceptance: fail closed at every step

A reading is recorded only if all of these hold:

1. The label crop OCRs to `Rating`. This is what keeps the reader off every
   other screen, including a menu the player has already clicked through to.
2. The white mask reads 3–5 digits within `RATING_MIN..RATING_MAX` (moved from
   `history_window.py` into `history/db.py`, so both write paths share one
   bound).
3. Exactly one of yellow or red has enough pixels. It sets the sign, and its
   mask reads 1–3 digits.
4. **Two consecutive captures agree** on (rating, delta). The number animates
   upward on the banner, and a read taken mid-count is a wrong number that
   looks right. Agreement between two frames ~400ms apart is the guard.

Any failure retries the loop until the window closes (below). The reader
never guesses a digit and never reads the sign from OCR.

### Timing

The banner comes up a few seconds after the game ends: in the captured game,
the concede was at 13:49:35.4 and `EndGameScreen` at 13:49:40.7
(`GameNetLogger.log`), and `GameEnd` itself waits on the placement tags. The
task polls every 400ms for up to `SCREEN_READ_SECONDS` = 30 after `GameEnd`,
and is cancelled by the next `GameStart`. A player who clicks through inside
the first second loses the automatic read and gets the nudge. That is the
correct fallback, not a bug.

### Catch-up must never read the screen

A historical `GameEnd` is a game that ended while the tracker was not watching,
so whatever is on screen belongs to something else. `Pipeline.handle` already
has the `historical` flag. Only the live path starts the reader.

### Events: the nudge moves from `GameEnd` to a miss

The pipeline emits exactly one outcome per `GameEnd`:

- `RatingRead(rating, delta)` once a reading is recorded. The overlay shows it
  in the status line (`MMR 6143 (+44)`) and raises no nudge.
- `RatingMissed(reason)` when the reader gives up, is disabled, or cannot run
  (no tesseract, no X display), and **immediately** for a historical
  `GameEnd`. The overlay raises the nudge on this event instead of on `GameEnd`.

Emitting the miss for historical games keeps the catch-up behaviour the prompt
spec settled: the nudge survives only when the session log really ends on a
finished game. It also means the nudge never flashes up and gets retracted
during the ~2s it takes to read the banner.

The reader follows `_shop_forecast`'s pattern: an asyncio task on the one loop
that notifies `listeners` when it finishes and swallows its own failures. No
thread (CLAUDE.md), and tesseract runs through
`asyncio.create_subprocess_exec`.

### Storage

`ratings` gains three nullable columns, each added with an `ALTER`. The
`_ADDED_COMBAT_COLUMNS` mechanism is generalised to take a table name, because
a column added only to `_SCHEMA` would be missing from the author's existing
database:

| Column | Meaning |
|---|---|
| `game_id` | the game this reading closes (screen reads only) |
| `delta` | the change the screen showed |
| `source` | `'screen'`; `NULL` means manual, which covers all 84 existing rows |

A partial unique index on `game_id WHERE game_id IS NOT NULL` makes a second
screen read of the same game an `INSERT OR IGNORE` no-op. Catch-up never
reads, so nothing replays these rows, but the index costs nothing and makes
that a guarantee rather than an assumption.

`Pipeline` must capture `_game_id` before `GameEnd` clears it.

### Read side

`review.game_list` prefers the delta the screen showed for a game, and falls
back to today's "only game between two readings" rule. That does not break
CLAUDE.md's no-interpolation rule. The screen's delta is Blizzard's number for
that one game; nothing is estimated.

`review` also flags a **gap** where a screen reading's `rating − delta` does
not equal the previous reading. The chain broke there: games were played
untracked, or a reading is missing. The history window marks the gap rather
than drawing a clean line through it. Period nets are unchanged: they already
diff readings, and there are now simply more of them.

CLAUDE.md's "MMR readings are manual snapshots" paragraph is rewritten in the
implementation commit, since it will no longer be true.

### Setting and doctor

One new key, `mmr_screen_read` (bool, default on, `overlay` section, channel
`NONE`: it is read fresh at each `GameEnd`). The help text says in plain words
what it does: it screenshots two small parts of the game window when a game
ends.

`doctor` gains a check: tesseract on `PATH`, `python-xlib` importable, and a
window named `Hearthstone` found when the game is running. tesseract is an
optional system dependency like `node`. When it is missing the feature reports
itself unavailable and the manual path is untouched.

## What this does not cover

- **Other client languages.** The label check is the English word `Rating`. A
  localised client fails closed on step 1 and gets the nudge every game.
- **Duos** has not been captured, and its banner may sit elsewhere. It fails
  closed the same way.
- **Aspect ratios other than 16:9** are untested. Centre-relative,
  height-scaled offsets are the best guess from how Unity lays out the UI.
- **The lobby screen** also shows the rating and would catch games the tracker
  missed entirely. It is a follow-up that reuses the same reader with different
  regions and a different trigger.

## Open before merging

- **A loss capture.** It confirms the red thresholds and becomes the second
  test fixture.
- **A 1st-place capture.** It confirms the banner layout is the same when you
  win the lobby.
- **The count-up animation**: when the digits settle relative to `GameEnd`. The
  two-agreeing-reads rule covers it either way, but the timing decides whether
  30s is generous or tight.

## Verification

- `pytest tests/` — whole suite. New coverage:
  - mask and parse against the win fixture, and the loss fixture once
    captured. These self-skip without tesseract, like `test_sim_roundtrip.py`
    without node.
  - the acceptance rules as pure functions over OCR strings and pixel counts
    (label mismatch, digit counts, both colours or neither, two-read
    agreement).
  - the `ALTER` on a database created with the old `ratings` schema.
  - `game_list` preferring a screen delta, and flagging a broken chain.
  - historical `GameEnd` emitting `RatingMissed` and never starting a capture.
- `test_settings_schema.py` for the new key.
- `--overlay --demo` gains a `RatingRead` so the status-line state renders in
  the skin pass.
- Live: three real games, one each for a gain, a loss and 1st place. Each must
  store a row whose `rating − delta` matches the previous reading.
