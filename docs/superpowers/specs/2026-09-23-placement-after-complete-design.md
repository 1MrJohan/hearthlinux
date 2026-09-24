# The final place can land after COMPLETE

## Problem

Game 408's banner said **6th Place**. The code on master records it as **7th**.

`GameEnd` is emitted at `STATE = COMPLETE`, carrying the place of the hero the
player entity names (`friendly_placement()`, fixed on 2026-09-23 to stop
reading a stale late copy). In game 408 that hero's place moved *after*
`COMPLETE`:

```
17:09:46.2157  our hero  PLAYER_LEADERBOARD_PLACE = 7   (lobby reshuffle)
17:09:46.9297  GameEntity STATE = COMPLETE              ← GameEnd(7) emitted here
17:09:46.9537  our hero  PLAYER_LEADERBOARD_PLACE = 6   ← the banner's number
```

The database happens to hold 6, because the game was recorded by the older
highest-id rule, whose late copy carried 6 that time. That rule was wrong in
9 of 17 other games, so it is no fallback.

`CLAUDE.md` already notes that "placement tags land a few packets *after*
`STATE = COMPLETE`". The deferral built for it only waits when *no* place
exists at `COMPLETE`. But the named hero carries a live leaderboard place all
game, so the deferral never waits.

## Evidence

The place of the named hero at `COMPLETE`, and every change to it afterwards,
in all 20 retained games that completed (2026-09-22 and -23):

| Game | At COMPLETE | After COMPLETE | Banner |
|---|---|---|---|
| 9/22 15:54 | 6 | 5 at +25ms | — |
| 9/22 17:03 | 4 | 3 at +54ms | — |
| 9/22 17:27 | 6 | 5 at +34ms | — |
| 9/23 13:06 | 7 | 8 at +0ms | — |
| 9/23 16:51 (408) | 7 | 6 at +24ms | **6th** |
| the other 15 | final | none | 3rd, 2nd confirmed |

- Every late change arrived within **54ms** of `COMPLETE` in log time, and
  there was only ever one.
- It moves in either direction (6→5, 7→8), so it is not a systematic
  off-by-one to correct for.
- The one game with a banner confirms the *late* value.
- 13:06 is already recorded right (8). Its change shares `COMPLETE`'s
  timestamp and so lands in the same exported packet.
- The other three are probably recorded one place worse than they finished.
  There is no banner to confirm it.

## Decision

**`GameEnd` stays where it is. A later change to the named hero's place
revises it.** The exporter keeps watching the named hero after `GameEnd` until
the next `CREATE_GAME` replaces it, and emits `PlacementRevised(placement)` on
every change to a value different from the last one it announced. The last
revision wins.

Downstream:

- `Pipeline` remembers the id of the game that just ended until the next
  `GameStart`, and writes the revision with a new
  `HistoryDB.set_placement(game_id, placement)`. It is a plain `UPDATE` of one
  column. `end_game` is not reused, because it also rewrites `final_turn` and
  would stamp `ended_at` if it were still null.
- The overlay updates the Game Over subtitle (`finished #N`). It updates the
  status line only while that still shows the placement; a rating read off the
  screen stays.
- The console prints the revision.
- Catch-up replays revisions like any other event, so
  `--replay FILE --record` over the retained logs repairs the three 9/22
  games. That is the same mechanism that re-records every other column.

### Why not wait before emitting `GameEnd`

The obvious fix is to delay `GameEnd` until the place settles. It fails on
timing that the log cannot guarantee:

- **Log time.** "Emit once a GameState line ≥ `COMPLETE` + 250ms arrives"
  needs a *later* line to arrive. After the conceder's or loser's final packet,
  the GameState stream can go quiet until the next game (game 409 did exactly
  that), and then `GameEnd` waits minutes. Its banner read and nudge would both
  miss.
- **Wall time.** The exporter has no clock and no loop; it is fed lines. A
  timer means moving end detection into the async pipeline, which is out of
  proportion to a 54ms race.
- **Feed batches.** "Emit at the end of the batch that held `COMPLETE`" works
  in a replay and usually live, because the tailer reads 256 KiB slices. But
  whether a 24ms gap straddles a poll boundary is luck, and luck is what this
  spec is removing.

A revision has none of these problems. The number on the overlay may change
once, 50ms after it appears, while the banner animation is still running.

### Rejected: trusting the late copy again

The highest-id copy was right in 408 and wrong in 9 others. Nothing in the log
says which kind of copy a game will produce.

## Scope

- One new event (`PlacementRevised`), one `HistoryDB` method, a `Pipeline`
  field, and a match arm each in the overlay and the console printer.
- No change to how `GameEnd` is triggered, to the concede path (an armed
  concede that ends on a place change can be revised the same way), or to the
  screen read.
- No automatic data repair. The replay that would repair the three 9/22 games
  writes to `history.db` and is run by hand, after a backup.

## Verification

- `pytest tests/`, with synthetic games covering: a change after `COMPLETE`
  emits one revision; an identical value emits none; two changes emit two, and
  the last is what the database holds; nothing is revised after the next
  `CREATE_GAME`; a revision reaches the database and the overlay subtitle but
  not an MMR status line.
- Replay of every retained log: 408 ends as 7 and is revised to 6. 15:54,
  17:03 and 17:27 are revised to 5, 3 and 5. The other 16 have no revision.
- `--overlay --demo` is unaffected (no new widget). This is checked by the
  existing render tests.
