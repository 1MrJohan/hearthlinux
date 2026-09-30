# Next-opponent forecast: making the actionable number legible

## Problem

Combat takes no input, so the fight forecast in the HUD is something to watch. The
shop forecast against your *next* opponent is the one number you can act on — it
re-runs as you buy, sell and reposition. Today it is the hardest number to use:

1. **You have to find the row.** It shows only while that opponent's leaderboard row is
   hovered, and nothing on the rail says which row that is, although the overlay holds
   `next_opponent_id` all recruit phase.
2. **It is unlabelled.** `41 / 12 / 47  ·  2 turns old` — the reader has to know the
   order is win / tie / loss.
3. **It drops lethal risk.** `SimResult.lost_lethal_percent` is computed for every shop
   forecast and discarded. The HUD already treats "you die" as the number that outranks
   any win percentage (`SimResult`'s own comment); the popout never shows it.
4. **It vanishes while you work.** Every `ShopBoard` (buy, sell, reposition) sets the
   forecast to `None` until the debounced re-run lands, so the line blinks out on every
   move while you are arranging the board — the exact moment you are using it.

## Design

**Rail marker: a Loss-coloured orb ring** on the next opponent's row, chosen by the user
over a `NEXT` tag (crowds a 98px rail) and a ⚔ glyph (font-fallback risk). It mirrors
`.orb.you` (the gold ring) with `$loss`, so no new token enters `theme.py`. Gold is
taken twice already — your own row and the hover border — so the marker must not be
gold. `set_next_opponent(player_id)` reaches the rail through the state model like
every other setter; the rail matches it against each row's `Standing.player_id`
(`PLAYER_ID`, the stable identity — not the row index, which reorders with places).
A dead opponent is never marked.

**Popout line:** `Win 41 · Tie 12 · Loss 47 · ☠ 9% · 2 turns old`. The lethal term
appears only at ≥ 1%, so a safe board does not carry a `☠ 0%`. Same `.board-odds`
style; no new CSS.

**Recalculating keeps the old line, labelled.** On `ShopBoard` the previous line stays
with its age replaced by `recalculating…`. This reverses one deliberate choice: the old
comment hid the number so it would not caption the new board with the old board's odds.
That concern holds for an *unlabelled* number; a line that says it is being recomputed
does not claim to describe the new board, and it removes a blink on every move. When
the re-run fails (`prediction is None`), the line still becomes "Odds unavailable for
this board" as now — a failed recompute must not leave the stale number standing.
`NextOpponent` still clears the line outright: odds against a different player are not
"the same number, recalculating".

## Out of scope

The HUD's generic "Odds unavailable" and the missing "simulating…" state (review item
4), and making the forecast visible without hovering. Both are separate decisions.

## Verification

Event tests for the line format, the lethal threshold, the recalculating carry-over,
the failure and new-opponent resets, and the rail marker following `PLAYER_ID` across a
reorder; `--overlay --demo` for the ring and the line.
