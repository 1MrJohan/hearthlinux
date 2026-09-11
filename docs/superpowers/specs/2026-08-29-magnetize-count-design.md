# Counting mechs magnetized to a minion

## Problem

Several current cards pay out per *magnetization*, not per stat point:
`BG26_152` Utility Drone gives your minions "+4/+4 for each Magnetization they
have", `BG31_176` Dr. Boom's Monster has "+2/+2 for each time you've Magnetized
this game", and `BG35_890` Ingenious Inventor is "improved by each time you've
Magnetized this game". The number that drives them is invisible outside the
card's own tooltip, and the tracker does not carry it.

## What the log gives, exactly

Evidence from the 2026-08-29 session (204k GameState lines) and the 3,233
combats in `history.db`.

**Each magnetized card leaves one enchantment on the host, tagged
`MAGNETIC = 1`.** One real host carried six:

```text
BG31_171te Satellite        BG26_147e   Accord-o-Tron
BG32_172e  Auto Assembler   BG_BOT_911e Annoy-o-Module
BG_DEEP_015e Prosthetic Hand BG35_341e  Enchanted Sentinel
```

`MAGNETIC`, `ATTACHED` and `TAG_SCRIPT_DATA_NUM_1` are all known to hslog's
`GameTag`, so nothing here needs a new parser capability.

**Repeats of the same card fold into that one enchantment**, growing its
script data rather than creating a second entity:

```text
ench 7755   12 → 16 → 20 → 24        four 4/4 Glambot satellites, one row
```

So counting enchantments counts *distinct linked cards*, never the total.

**There is no magnetization-count tag.** `MODULAR_ENTITY_PART_1/2` exist in the
enum and appear zero times in the session. The one plausible unknown tag,
`4741`, matched the magnetic-enchantment count in 0 of 14 real hosts and the
total-enchantment count in 0 of 14 — it resets to 0 and is a per-turn trigger
counter.

**Every magnetize is exactly one write.** All 67 satellite growth steps in the
session are `+4`, against a 4/4 source. No batched double-grant was observed —
but the sources that grant two at once (`BG31_171` Moonsteel Juggernaut,
`BG32_174` Electron, `BG34_177` Double Demolisher) do not appear in this
corpus, so a single write covering two magnetizations is unverified rather
than ruled out.

**Minions are recreated every turn.** No entity in the session spans two raw
turns, and no minion carries `COPIED_FROM_ENTITY_ID` (3,988 lines, heroes
only). The accumulated stats *do* carry: turn 21's enchantment ends at 48 and
turn 23's equivalent starts at 52. Six hosts in one game held an identical
six-enchantment set — the same logical minion, six times.

## Decision

Ship what the log states, and do not ship the count.

`Enchantment` gains a `magnetic` flag, read straight from the tag. From it,
`Minion.linked_cards` is the number of distinct magnetic cards attached and
`Minion.magnetized_stats` is the attack/health they contributed. Both are exact,
both work for opponents, and both are already true of every combat stored in
`history.db`.

The total number of mechs is deliberately absent. It is the number that was
asked for, so the attempt is recorded here rather than left for someone to
repeat.

## The counting attempt, and why it was rejected

Counting looked feasible because every magnetize is a write: the folded
enchantment's `TAG_SCRIPT_DATA_NUM_1` grows once per magnetized mech. A tracker
was built on that — one increment per observed growth, with minions re-linked
across the per-turn recreation by matching the carried total the new
enchantment is born holding.

It does not survive the real log.

- The enchantment is **created empty and filled by a later tag change**. That
  first fill is the carried total on a recreated minion and the newcomer's
  stats on a fresh magnetize, and nothing in the entity distinguishes them. A
  host-novelty rule (only a host some projection has already reported on can be
  magnetized *now*) fixes the obvious cases and still leaks, because projections
  are coalesced to the end of an input batch — several magnetizes inside one
  batch reach the tracker before any projection has seen the host.
- Replayed over the 2026-08-29 session the counts were **not monotonic**: the
  same board slot read 5, then 3, then 7 across consecutive turns, which no
  cumulative count can do.
- Checked against the one independent oracle — Utility Drone's `BG26_152e`
  grows each turn by 4 × that minion's magnetization count — the tracker agreed
  on **0 of 9** comparisons and undercounted by up to 7×. A golden Glambot host
  carrying +50 of satellite stats from 8/8 satellites was reported as 2.

A number that says 2 where the truth is closer to 12 is worse than no number,
and this repo already refuses that trade for tavern tier and for shop buffs.

**What a future attempt needs** is a per-magnetize signal that does not depend
on diffing tag values: the `BLOCK_START`/`TRIGGER` structure around each
magnetize, which hslog does expose as packets, is the obvious candidate. It
should be judged against the Drone oracle on a captured game before anything
reaches the overlay.

## Not sent to the simulator

The host's live `ATK`/`HEALTH` already include everything magnetized into it,
so the mapper is already correct and `globalInfo` is unaffected. Sending a
count would at best duplicate stats the board already carries. This is a
display value only.

## Verification

- `pytest tests/` — synthetic coverage that a magnetized mech reads as one
  linked card, that an ordinary buff enchantment does not, that four folded
  Satellites are one card carrying 16/16, and that an opponent's linked cards
  are readable off the combat board.
- The 2026-08-29 session replayed end to end through `LiveGameProcessor`, which
  is where the counting attempt failed and where any future one has to pass:
  counts must be monotonic per board slot and must match the Drone oracle.
