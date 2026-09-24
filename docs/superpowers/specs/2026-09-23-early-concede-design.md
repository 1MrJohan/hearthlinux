# Ending a game the player conceded early

## Problem

Game 409 (2026-09-23, 17:18) has no placement, no end time, no MMR read and
never raised the Record MMR nudge. The player conceded at 17:24:01 on turn 5,
with all eight players alive, and the rating fell 97 (6217 → 6120, typed by
hand afterwards).

`GameEnd` is gated on `STATE = COMPLETE` on the game entity
(`parse/exporter.py`), and an early concede never logs one. The lobby carries on
without the player, so from our side the game simply stops: the last GameState
packet is at 17:24:01.593 and the next line is the following game's
`CREATE_GAME` at 17:25:10. `LiveGameProcessor` then starts a fresh parser, and
`finalize()` flushes a pending `GameEnd` only once `COMPLETE` has been seen. So
the conceded game is dropped without ever ending.

The row was repaired by hand (placement 8, ended 17:24:01, final turn 5). This
spec is about the next one.

## What the log shows at the concede

One GameState packet at 17:24:01.593, and nothing after it:

| Entity | Tag | Value |
|---|---|---|
| our player entity (`Johan#12827`) | `3479` | 1 |
| our player entity | `4356` | 1 |
| `GameEntity` | `4302` | 1 |
| our hero (the one `HERO_ENTITY` names) | `PLAYER_LEADERBOARD_PLACE` | 8 |

The three numbered tags are not in the `hearthstone` package's `GameTag` enum.
They are not dropped: hslog passes a numeric tag through as an int, and all
three reach `BGExporter.handle_tag_change`. Unknown *named* tags are the ones
that raise `NoSuchEnum`.

Across the 18 games in the retained session logs at the time (2026-09-22 and -23), **none
of the three tags appears anywhere except this concede**. The 17 games that
ended normally, including two where `GameNetLogger.log` also says "Player
conceded the game" (13:49, 16:47), carry none of them. In those two, the
player left from the end screen after `COMPLETE`; the log line is not specific
to an early concede.

The place set in that packet is final: a concede with eight alive is 8th, and
the −97 fits it.

## The second sample

Game 411 was conceded at 17:58:47 on turn 8 (7th). Its packet differs:

| Entity | Tag | Value |
|---|---|---|
| our player entity | `PLAYSTATE` | `CONCEDED` |
| our player entity | `3479` | 1 |
| our player entity | `PLAYSTATE` | `LOSING`, then `LOST` |
| `GameEntity` | `STATE` | `COMPLETE`, 36ms later |

No `4356` and no `4302`, and the game *did* complete, so the existing path
already ended it correctly (7th, and its banner was read: 6009 −70). Our hero's
place did not change in the packet; it was already final.

Game 409 never set `PLAYSTATE` at all. The one tag the two concedes share is
`3479` on our own player entity, and it appears in none of the 17 normal
endings, all of which go `LOSING → LOST` (or `WON`) and then `COMPLETE`.

## Decision

**`3479 = 1` on the local player entity marks a concede. It arms the game's
end but does not end it.** In game 409 the place changed to 8 *after* `3479`
in the same packet; ending on the marker itself would have recorded the live
place from before the concede (4th). So after the marker, the game ends on
the first of:

1. **a place change on the hero `HERO_ENTITY` names**: game 409, 4 → 8. Place
   changes on *other* heroes are ignored while armed, because the lobby
   reshuffles as the conceder drops out.
2. **`STATE = COMPLETE`**: game 411, through the existing path.
3. **`finalize()` at the next `CREATE_GAME`**, with the named hero's place as
   it stands. A place that never changed after a concede was already final
   (game 411 again). This is late for the banner, so the reader misses and
   the nudge asks. It is the fallback, not the path.

Both concedes then carry the right placement, and 409's `GameEnd` lands at
17:24:01, five seconds before its banner.

- **`4356` and `4302` are not used.** Each appeared in one concede only.
  `4302` is also on the game entity, so nothing shows it would not be set by
  an *opponent's* concede.
- **`3479` stays a number**, as a named constant with this spec beside it.
  Naming it `CONCEDED` in an enum would claim more than two samples show.
- **Only our player entity counts.** An opponent's `3479` must not end our
  game.

### Rejected

- **Ending on `PLAYSTATE = CONCEDED`.** It is named and obvious, and game
  409, the case this spec exists for, never set it.
- **Closing any unfinished game at the next `CREATE_GAME`.** That would
  record a live leaderboard position as final for every game whose end was
  missed for any reason, such as a truncated log or a crash. `finalize()`
  does this only for a game already armed by a concede.
- **Tailing `GameNetLogger.log` for "Player conceded the game".** That line
  also appears when leaving a normal end screen (13:49, 16:47), and the
  tracker's only input is `Power.log` by design.

## Verification

- `pytest tests/`, with synthetic concedes covering: our place change ends
  the game with the new place, with no `COMPLETE`; another hero's place
  change while armed does not; `COMPLETE` after a concede ends it once;
  `finalize()` ends an armed game with the current place; `3479` on the
  opponent and `4302` alone do nothing.
- Replay of every retained session log: game 409 ends as 8th in its concede
  packet, and all 19 other games end exactly as they do today.
- Live: the next early concede records its place and reads its banner.
