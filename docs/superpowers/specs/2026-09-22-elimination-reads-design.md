# Elimination reads: combat-copy resets and lethal wins

Two defects with one root: the tracker's notion of "this player is dead" is read from
hero entities, and since at least 2026-09-20 every combat leaves behind a hero entity
that *looks* dead for an instant without being so.

## Symptoms

1. **The rail marks living opponents eliminated.** After a player's first fight against
   you, their portrait greys out, their HP is struck through and the scout popout says
   "eliminated". Traced on the 2026-09-22 15:20 session: game 392 had players
   7, 1, 5, 2, 6, 4, 8 latched dead after turns 1 to 7 respectively, every one of them
   alive.
2. **A win that eliminates the opponent is recorded as `unknown`.** Game 392 turns 10 and
   13 were forecast 100% win for 15 against opponents on 5 and 7 HP; both were written
   with `outcome = NULL` and the HUD showed no result. 330 of 4,224 stored combats have a
   NULL outcome, 244 of them forecast as wins and 184 at 100%, so the calibration
   table's top bucket has been scored without most of the fights it got right.

## Log evidence

**The combat hero copy is reset, not killed.** Each fight materializes a copy of both
heroes (`BACON_COMBAT_PHASE_HERO = 1`, carrying the owner's `PLAYER_ID`). When the fight
is cleaned up, the game moves the copy out and zeroes it before restoring it, all at one
timestamp:

```
TAG_CHANGE Entity=[… id=552 … cardId=BG36_HERO_101] tag=ZONE value=REMOVEDFROMGAME
TAG_CHANGE Entity=[… id=552 …] tag=HEALTH value=0
TAG_CHANGE Entity=[… id=552 …] tag=ARMOR value=0
…
TAG_CHANGE Entity=[… id=552 …] tag=HEALTH value=30
TAG_CHANGE Entity=[… id=552 …] tag=ARMOR value=15
```

Every retained session log (2026-09-20 onward; nothing older is still on disk) shows
this: 62, 34, 30, 21 and 152 zeroing resets against 124, 68, 60, 44 and 316 copies.

`8974223` (2026-08-29) moved the death latch into `handle_tag_change` so a Kel'Thuzad
ghost could not un-kill a player later in the same input batch. That latch reads
`HEALTH - DAMAGE <= 0` on *any* hero entity with a `PLAYER_ID`, at the packet, so it
sees the transient `HEALTH=0`. The leaderboard pass never did: it runs after the batch,
once the value is restored.

**A real lethal is visible before the fight ends — but only on the copy.** Game 392,
turn 10, opponent `PLAYER_ID` 5 on 5 HP:

| Line | What | Relative to combat end |
|---|---|---|
| 208700 | copy (PLAY) `DAMAGE=40` against `HEALTH=30` | before |
| 208769 | copy `ZONE=GRAVEYARD` | before |
| 211031 | `BOARD_VISUAL_STATE=1` — `CombatEnd` is emitted here | — |
| 211374 | copy reset: `REMOVEDFROMGAME`, `HEALTH=0`, `HEALTH=30` | after |
| 211569 | the real leaderboard hero (SETASIDE) `DAMAGE=40` | after |

At `CombatEnd` the dead opponent's copy is in GRAVEYARD, so `snapshot()` finds no
opponent hero in PLAY and `end.opponent` is `None`; `_classify_outcome` then returns
`None`. The real hero only learns it is dead after the event has gone out.

## Decision

1. **Do not latch a death off an entity already in `REMOVEDFROMGAME`.** All 302
   `HEALTH=0` changes on hero entities in the retained logs follow a move to that zone,
   so the reset is excluded while a genuine death — `DAMAGE` in PLAY, then GRAVEYARD —
   still latches at its packet, which is what the Kel'Thuzad case needs. The
   leaderboard pass is unchanged.
2. **`CombatEnd` carries the players eliminated so far** (`eliminated: frozenset[int]` of
   `PLAYER_ID`s), and `_classify_outcome` reads a non-ghost opponent in that set as a
   `win`, after the loss and ghost checks and before the HP comparison. The damage for
   such a fight stays 0 — the overkill is not known at `CombatEnd` — so the HUD shows
   `✔ WON` without a number rather than an invented one.

Alternatives rejected:

- **Exclude `BACON_COMBAT_PHASE_HERO` entities from the latch.** The copy is the only
  entity that shows a lethal before `CombatEnd`; excluding it would fix symptom 1 and
  make symptom 2 unfixable.
- **Defer outcome classification until the real hero updates.** `CombatResult` is already
  banked until `ShopReady`, but history and the rail would then depend on a later packet
  whose timing has no guarantee, to fix a case the copy already answers.
- **Infer a lethal from the forecast or the opponent's absence later in the game.**
  Guessing the label the calibration table is scored against is the one thing this must
  not do.

## Verification

Replaying every retained session with the rule applied:

- The only fights where the opponent reads dead at `CombatEnd` are the 14 lethal wins,
  and they are exactly the fights whose `end.opponent` is `None`.
- The 5 Kel'Thuzad ghost fights still classify as `ghost`; none reads as a death.
- In all 12 games the final leaderboard marks eliminated exactly the opponents whose HP
  reads `<= 0`, and no living one.

Stored rows are corrected by re-recording the retained sessions (`--replay FILE
--record`, without `--odds`). `record_combat` guards the outcome and every prediction
column with `COALESCE`, so this fills NULL outcomes and leaves recorded predictions alone;
the boards are rewritten from the same log. Sessions older than 2026-09-20 are gone and
their rows stay NULL.
