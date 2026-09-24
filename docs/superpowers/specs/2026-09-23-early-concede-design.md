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

Across all 18 games in the retained session logs (2026-09-22 and -23), **none
of the three tags appears anywhere except this concede**. The 17 games that
ended normally, including two where `GameNetLogger.log` also says "Player
conceded the game" (13:49, 16:47), carry none of them. In those two, the
player left from the end screen after `COMPLETE`; the log line is not specific
to an early concede.

The place set in that packet is final: a concede with eight alive is 8th, and
the −97 fits it.

## Decision

**Treat the local player entity's tag `4356` or `3479` becoming 1 as a
concede, and end the game there** with the place of the hero `HERO_ENTITY`
names, as `friendly_placement()` already reads it. That is live, at the moment
the banner is about to appear (`EndGameScreen` was 5s later), so the screen
read and, failing that, the nudge both work unchanged. Nothing downstream of
`GameEnd` changes.

- **Player-scoped tags only.** `4302` sits on the game entity, and nothing
  yet shows whether an *opponent's* concede would set it in our log. Ending
  our game on someone else's concede would be worse than today's silence.
  `3479` and `4356` are on our own player entity, so they cannot be about
  anyone else.
- **Deferred like `COMPLETE`.** The concede sets `_ended` and goes through
  `_maybe_emit_end()`, so the place tag landing a few lines later in the same
  packet is still picked up, and a concede with no place still ends via
  `finalize()`.
- **The existing `COMPLETE` path is untouched.** A game that ends normally
  never sets these tags; if a later patch starts setting them at a normal end,
  `_end_emitted` makes whichever arrives first the only `GameEnd`.

### Rejected

- **Closing an unfinished game at the next `CREATE_GAME`.** This works for any
  cause and needs no unknown tags, but it is ~70s late for the banner, and it
  would record a *live* leaderboard position as final. It would do that for
  every game whose end the tracker missed for other reasons too, such as a
  truncated log or a crash mid-game. A wrong placement recorded as fact is
  worse than a blank one.
- **Tailing `GameNetLogger.log` for "Player conceded the game".** That line
  also appears when leaving a normal end screen, and the tracker's only input
  is `Power.log` by design.
- **Naming the tags.** Adding `3479`/`4356` to a local enum would make the
  code read better and claim a meaning we have not established. They stay
  numbers, as constants with this spec's evidence beside them.

## Open before implementing

**A second early concede.** One sample cannot say which of `3479`/`4356`
means "conceded" rather than "left the game" or "was eliminated". The second
sample decides three things:

1. whether both tags recur. The trigger keeps only the tags present in both
   samples.
2. whether either appears for an ordinary elimination. The 17 normal games say
   no, but only a few of them were eliminations before the final two.
3. whether the place in the concede packet is still the final one when players
   are already dead (a concede at 5 alive should read 5).

## Verification

- `pytest tests/`, with a synthetic concede built in `tests/synthetic.py`:
  `GameEnd` on the tag, with the named hero's place; no `GameEnd` on `4302`
  alone; no second `GameEnd` when `COMPLETE` follows.
- Replay of `Hearthstone_2026_09_23_17_15_53/Power.log` (captured as a fixture
  once trimmed): game 409 must end as 8th at 17:24:01. Every other retained game
  must end exactly as it does today.
- Live: the next early concede records its place and reads its banner.
