# Undead and Beetle bonuses: forwarding them as globalInfo

## Problem

Two board-wide bonuses reach the Buffs panel but not the simulator:

| Bonus | Entity | Read | Buffs panel |
|---|---|---|---|
| Undead attack | `BG25_011pe` "Undead Bonus Attack Player Enchant [DNT]" | `NUM_1` | `read_played_buffs` |
| Beetle Army | `BG31_808pe` "Beetle Army Player Enchant [DNT]" | `NUM_1`, `NUM_2` | `read_played_buffs` |

`_global_info` sends only the eight player-tag keys (Blood Gem, Elemental, Pirate,
Tavern Spell). These two are player-level *enchantments*, not player tags, so they were
never forwarded. CLAUDE.md's "Undead has no player counter — it is tracked per-minion"
is wrong in the way that mattered: there is no player *tag*, but there is a player
enchantment carrying the running total.

## What the simulator does with them

Simulator 1.1.755:

- `add-minion-to-board.js` adds `globalInfo.UndeadAttackBonus` to every Undead summoned
  mid-combat, and subtracts it again when one is removed; `deathrattle-orchestration.js`
  subtracts it from a dead Undead before its reborn copy is made. The model is that live
  stats already include the bonus, which they do — so with it absent, reborn and
  summoned Undead come in weaker than the game makes them.
- `cards/impl/minion/beetle.js` adds `BeetleAttackBuff/HealthBuff` to every Beetle
  summoned, and Forest Rover, Runed Progenitor, Silky Shimmermoth and Ravaging Scorpid
  raise the counters in combat — from a baseline of zero when it was never sent.

Firestone's own converter (`BattlegroundsPlayerBoardParser.BuildGlobalInfo`) reads both
exactly the way the Buffs panel does: the first enchantment with that card id in `PLAY`
under the player's controller, `NUM_1` (and `NUM_2` for Beetle Army).

## Log evidence

The six session logs from 2026-09-23/24 (57 combats) parsed with the new read:

- 9 friendly snapshots carry a bonus (Beetle Army +2/+1 across one game) and **3
  opponent snapshots do** (`UndeadAttackBonus` 1 and 7, and a Beetle Army at +83/+83).
  The opponent's enchantment sits in `PLAY` under the combat controller when the board
  materializes, so the read is not friendly-only.

## Design

`project_player_board` collects those two enchantments during the walk it already does
over the controller's entities, and merges them into `global_info` under the simulator's
key names. Nothing changes in the mapper — `global_info` is forwarded as it stands —
and the stored board JSON gains the keys automatically.

Rows stored before this carry no such keys and simulate exactly as they did. That is
not a fail-closed case like the deity secret: the old payload was the status quo, not a
known-wrong value, and blocking every Undead or Beetle fight in history would throw away
calibration data to protect against an omission already priced into it.

## Verification

- Unit: both enchantments land in `global_info`, and only in `PLAY`.
- Effect, measured on the ten session-log fights with a nonzero bonus, each simulated
  with and without it and joined to its recorded outcome: one moved materially (turn 5,
  loss 19% → 0%, win 29% → 88%; it was a tie, so the loss side moved toward the
  outcome). The other nine were decided boards either way, including the +83/+83 Beetle
  opponent. That is too small a sample to call an accuracy gain; the case for the change
  is parity with the converter Firestone itself uses, and the corpus will grow now that
  new rows record the values.
- `resim` cannot see the change: no stored row has the new keys.
