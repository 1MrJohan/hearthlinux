# Sending the lobby's tribes to the simulator

## Problem

Firestone's `gameState.validTribes` narrows the minion pool that combat-time
summons draw from (`cards-data.js::inititialize` filters `baconMinions` by
tribe before building `minionsForTier` and the Ghastcoiler spawn list). The
sidecar already forwards the field (`sidecar/sim-worker.mjs`), but the mapper
never sends it, so every "summon a random minion" effect draws from all ten
tribes instead of the lobby's five. This is the largest known accuracy gap
that is neither fail-closed nor already reflected in the live snapshot.

## What the log gives, exactly

Evidence from the 2026-09-10 session (four games, 126 MB of `Power.log`) and
the fourteen games recorded to `history.db` that day.

**No entity states the lobby's tribes.** The only `GameTag` names containing
`RACE` are `CARDRACE`, `MAGNETIC_TO_RACE`, `RACE_ALSO_UPDATE_COUNT` and
`EMBRACE_THE_SHADOW`; none is a per-game list. Nothing under the `BACON_`
prefix names the lobby either. The other enabled loggers (`Zone`, `Bob`) and
the client's own `Hearthstone.log` mention tribes only through
`BG_ShopBuff_MultiRace_Ench` asset misses.

**`BACON_SUBSET_<TRIBE>` is a per-minion flag, not a lobby list.** Twelve
such tags exist (`DRAGON`, `MURLOC`, `DEMON`, `BEAST`, `MECH`, `PIRATE`,
`ELEMENTALS`, `QUILLBOAR`, `NAGA`, `UNDEAD`, `TOTEM`, `ALL`). They ride on
individual minion entities, toggling 0/1 as the minion is created and moved,
2,300 times a session. Per game the in-lobby tribes dominate by count — game
2 read Demon 85, Pirate 84, Naga 71, Dragon 58, Beast 32 — but out-of-lobby
tribes still appear in small numbers (Elemental 16 in that game; Dragon 4,
Demon 14, Pirate 13, Undead 8 in game 1), because tokens, hero-power minions
and hero-select art carry the same flag. A frequency cut would be a
heuristic, and its threshold is not stated anywhere.

**Boards are not an exact oracle either.** The union of tribes on every
recorded board of a game is polluted the same way: a game that was plainly
Elemental/Naga/Pirate/Mech showed one Murloc and three Demons across its
combats. So there is nothing in `history.db` to *verify* a heuristic against
without a hand-written note of what the lobby actually offered.

## Decision

Do not send `validTribes`. The pool stays unfiltered, which is the
simulator's own default and is *uncertain* rather than *wrong*: a summon may
pick a tribe the lobby lacks, but no in-lobby tribe is ever excluded. A
frequency-derived list that dropped a real tribe would fail the other way,
silently, and this repo already refuses that trade for tavern tier and shop
buffs.

## What a future attempt needs

1. **An oracle.** Note the five tribes shown on the hero-select screen for
   ten or more games, keyed by the game's `log_id`. Nothing in the log can
   stand in for this.
2. **A rule judged against it.** The obvious candidate is "the tribes whose
   `BACON_SUBSET_*` flag has been set on at least N distinct minion entities
   by raw turn T". Pick N and T from the oracle games, then confirm on games
   the rule was not tuned on.
3. **Gating.** Send the list only once it has reached the lobby's size and
   held stable for a turn; before that send nothing. A partial list is the
   failure mode, not a lesser version of the feature.
4. **`resim 800` in both directions.** Stored combats carry no tribe list,
   so the harness must derive one from the stored game's log or skip those
   rows; judge by Brier over the rows it could derive one for.

Not attempted here because step 1 is a data-collection task across future
games, not a code change.
