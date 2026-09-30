# Combat odds states: simulating, and why there are none

## Problem

The HUD has one state for "no odds" and none for "odds on the way":

1. **No simulating state.** `Pipeline.handle` awaits `_simulate` before fanning
   `CombatStart` out. Until the first partial arrives as a `CombatForecast` the HUD
   still shows the recruit phase — title, meta, and the *previous* fight's pinned
   `✔ WON · dealt 14`. Under `SCHED_IDLE` on a busy CPU that gap is visible.
2. **Every cause reads the same.** `Odds unavailable for this combat` covers a hero power
   off the allow-list, a hidden secret, an incomplete deity secret, an incomplete board,
   a card the pinned simulator cannot model, a timeout, a sidecar crash, the simulator
   not running at all, and a restart into a live fight (catch-up never simulates). The
   reason already exists — `simulation_blocker()`, the exception — and reaches only
   `print` and the log, which nobody reads when the game launches the tracker.
3. **A failed run wipes real numbers.** If partials were on screen and the final
   request raises, `_clear_forecast` blanks them.

## Design

**`CombatSimulating(snapshot)`**, a new derived event, fanned out by the pipeline
*before* it awaits the simulator — only when a run will actually start (live, simulator
present, no blocker). The overlay enters combat on it: title, meta, cleared previous
result, status `Simulating…`. Partials and the final result then replace it as now.

**`OddsUnavailable(reason)`**, a derived event fanned out right after a `CombatStart`
that carries no prediction, mirroring `RatingMissed(reason)`. `_simulate` returns the
reason alongside the result; the overlay shows `No odds · <reason>`. Reasons are short
and name the thing when there is one:

| Cause | Reason |
|---|---|
| catch-up (historical batch) | `tracker started mid-fight` |
| no simulator | `simulator not running` |
| off-list combat hero power | `<power name> isn't simulated yet` |
| allow-listed power, state unrecorded | `<power name> state not recorded` |
| hidden secret | `a secret is hidden` |
| deity secret without `NUM_6` | `Old God secret incomplete` |
| board incomplete | `a board isn't fully visible` |
| `UnsupportedCombatCardsError` | `simulator can't model <card>` (first two names, `…` beyond) |
| timeout | `simulator timed out` |
| sidecar died / other error | `simulator crashed` / `simulator error` |

The blocker strings are mapped in `app.py`, not changed in the mapper: they are also
what the console prints and what tests pin, and a mapping with a test per blocker keeps
both honest.

**Partials survive a failed run.** When partial odds for this fight are on screen and
the final result is `None`, the overlay keeps them and the status says
`Provisional · <reason>`. The recorded prediction stays `None` — a partial is shown, not
scored.

## Out of scope

The shop forecast's `Odds unavailable for this board` keeps its wording; its failures
are the same causes and can adopt the reasons later. Pipeline health beyond a single
fight (log.config written, no Power.log) is review item 5.

## Verification

Pipeline tests for the event order (simulating before the result, unavailable after the
start) and each reason; overlay tests for the three HUD states; a test that every
`simulation_blocker` string maps to a specific reason; `--overlay --demo` gains a
simulating beat and a named-reason beat.
