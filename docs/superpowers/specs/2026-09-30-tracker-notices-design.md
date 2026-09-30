# Tracker notices and simulator retry

## Problem

The tracker is launched by the game, so its console goes nowhere anybody reads. Three
startup conditions reach only that console:

- `log.config written — restart Hearthstone for logging to take effect`. Until the
  restart the game writes no `Power.log`, and the HUD reads `waiting for game…` forever.
- `no Power.log yet this session…` after 90s — logging is broken, and nothing on screen
  says so.
- `combat simulator unavailable — odds disabled`. `start_sim` pings once; on failure it
  returns `None` and the pipeline has no simulator for the rest of the session.

The last one cost real games on 2026-09-30. A system update (simdjson 4.6 → 5.0) broke
`node` at 13:41; the tracker started at 13:47, failed its one ping, and ran the next two
games — 7 and 16 fights — with no odds at all. Node was repaired later that afternoon,
but nothing would have retried: odds needed a restart to come back, and the HUD said only
"Odds unavailable".

## Design

**The simulator is retried, with a backoff.** `start_sim` always returns the client.
`SimClient._ensure_proc` raises `SimulatorUnavailable` when the sidecar cannot be
spawned or exits before its `ready` line, and remembers the failure: for the next 30s
it raises again without spawning, so a broken install costs one failed spawn per 30s
rather than one per fight and one per debounced shop forecast. After that it tries again
on the next request, so odds return by themselves once the install is fixed.
`describe_failure` maps it to `simulator not running`.

**`TrackerNotice(kind, text)`**, fanned out through the pipeline's listeners by
`Pipeline.notice(kind, text)`. `text = None` clears that kind. Kinds and wording:

| Kind | Raised when | Text | Cleared when |
|---|---|---|---|
| `log` | `log.config` was just written | `Restart Hearthstone once to turn on its game log` | a `GameStart` arrives |
| `log` | no `Power.log` 90s into a session | `No game log yet — if you're in a match, restart Hearthstone` | a `GameStart` arrives |
| `sim` | the startup ping fails, or a run hits `SimulatorUnavailable` | `Combat simulator not running — odds are off` | a run succeeds |

The overlay shows a notice in the HUD status line **only while idle** (no game on
screen): in a game the combat reasons of the 2026-09-30 odds-states work already say
`No odds · simulator not running` per fight, and a notice must not overwrite the phase
status the player is reading. `log` outranks `sim` when both are set, since without a log
nothing else can work. Clearing a notice while idle restores `waiting for game…`.

## Verification

Client tests for the backoff (no respawn inside the window, one retry after it, recovery
on a working spawn) against a fake `node` on `PATH`; pipeline tests for raising and
clearing each kind; overlay tests that a notice shows idle, not mid-game, and that `log`
outranks `sim`; `--overlay --demo` gains an idle notice beat.
