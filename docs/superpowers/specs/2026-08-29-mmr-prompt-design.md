# End-of-game MMR prompt design

## Problem

MMR readings are manual snapshots — the log never carries a rating, so every
number in `ratings` was typed by hand. The one moment the player actually knows
their new rating is the post-match screen, and that is exactly the moment
nothing asks for it. Recording one today means remembering to, then either
running `bgtracker mmr <n>` in a terminal the game-launched tracker does not
have, or gear → Settings → Match history → type. The result is the gaps the
read side already bends over backwards to be honest about: `_net_mmr` returns
`None` rather than guess, and a game gets a delta only when two readings
isolate it. Most of that abstention is not a data-model limit, it is simply
readings that were never taken.

So: ask at the moment the answer is on screen.

## What rules this out of the obvious shape

The obvious shape is a dialog that appears on game end with the entry focused —
type, Enter, done. Two constraints stop the overlay from being where that
happens:

- The overlay is a layer-shell surface with `KeyboardMode.NONE`
  (`overlay/window.py:71`). It cannot receive a keystroke, by construction.
- Its input region is *almost empty*, and deliberately: the gear is the only
  patch of pixels on a locked overlay that swallows a click instead of passing
  it to Hearthstone, which is what keeps the game's own board-preview-on-hover
  working. `_input_rects()` is the whole list and every entry is a hole in that
  guarantee.

Flipping the surface to `KeyboardMode.ON_DEMAND` while a prompt is up would
buy inline typing at the cost of inverting both, and behaves differently per
compositor. An auto-opening focused dialog avoids the layer-shell problem but
takes keyboard focus from Hearthstone at a moment the player may already be
re-queuing.

## Decision

Split it: the overlay **nudges**, the ordinary window **collects**.

On `GameEnd`, a single small button appears on the overlay — styled like the
gear, parked just below the HUD, reading `⊕ Record MMR`. Clicking it opens the
existing Match History window with the rating entry focused, and hides the
nudge. Nothing about the keyboard mode changes, and the click-through
guarantee grows by exactly one rect, only while the nudge is up.

The window it opens already owns the entry, the sanity bounds
(`RATING_MIN/MAX`), the validation message and `db.record_rating`. This adds no
second way to write a rating.

### One rect, not two

The sketch had a `✕` beside the button. It is dropped: a second rect is a
second hole in click-through, permanently, to buy a dismissal that only
anticipates the timeout below. The nudge clears three other ways.

### Lifecycle

Raised on `GameEnd` when `mmr_prompt` is on. Cleared:

- on click (the window is open; the nudge has done its job),
- on the next `GameStart` — a new game means the moment has passed,
- after `MMR_PROMPT_SECONDS` (120), so an ignored nudge does not leave a hole
  in click-through over the game's menus for the rest of the session.

Placement is `None`-tolerant: `GameEnd` can carry no placement (`finalize()`
flushes one), and a finished game is worth recording either way.

### Catch-up replays it, and that is the right outcome

Listeners are handed `(event, prediction)` with no `historical` flag
(`app.py:106`), so a `GameEnd` drained during catch-up raises the nudge like
any other. It needs no special case: every catch-up game but the last is
followed by a `GameStart`, which clears it. The nudge therefore survives
catch-up only when the session log genuinely *ends* with a finished game —
which is precisely when it should be up.

### It lives in the state model, not in the widget

`OverlayState` gains `mmr_prompt: bool = False` and `render` gains a
`window.set_mmr_prompt(...)` call, like every other displayed value. Anything
else would lose the nudge on an `overlay_scale` rebuild, which is the bug the
state model exists to prevent.

### Position

The gear owns the space *above* the HUD's top-right corner; the nudge takes
*below* the HUD's bottom-right. Same reasoning as the gear: empty space beside
a panel the user positioned, never over the panel's own content. It re-places
whenever the HUD moves, and — the trap `_place_gear` documents — a move that
did not re-upload the input region would leave the clickable patch behind at
the old spot, invisible and swallowing clicks meant for the game.

### Setting

One new key, `mmr_prompt` (bool, default on, `overlay` section, `OVERLAY`
channel). The channel is not decorative: turning it off has to hide a nudge
that is on screen right now, or the setting would be the one thing in the
window that does not apply live.

Off leaves both existing paths untouched (`bgtracker mmr <n>`, and the entry in
the Match History window).

## Verification

- `pytest tests/` — with coverage for: the `GameEnd` → `mmr_prompt` transition
  and its three clearings; `render` emitting `set_mmr_prompt` only on change;
  the nudge contributing exactly one rect to `_input_rects()` while visible and
  none while hidden.
- `--overlay --demo` — the demo script gains a `GameEnd`, so the nudge is a
  state the skin pass actually renders rather than one only a real game
  produces.
