# Keeping the simulator off the game's cores

Date: 2026-07-28

## Problem

The tracker makes Hearthstone hitch. Two moments, both reported by the author
and both explained by the same thing:

- **during the recruit phase, while clicking** — buying, selling, dragging;
- **at the shop→combat transition** — a hitch as the fight starts.

Those are exactly the two moments the simulator runs. Nothing in the tracker
tells the scheduler that the simulator is background work, so four CPU-bound
Node worker threads compete for cores at normal priority against a game that
needs a frame every 16ms.

The history database already measures one of the bursts. Across 589 recorded
combats:

```
sims_run   min 8000   mean 8000     max 8000      (never truncated)
sim_ms     min 17     mean 387      max 1859
```

So the combat forecast is four threads at 100% for a mean of 387ms and a worst
case of **1.86 seconds**, and it lands on the frame where the game is starting a
combat. The shop forecast is the same four workers at `SHOP_SIM_COUNT = 2000`
(so roughly a quarter of the time), re-fired on every board change after a 300ms
debounce — buys, sells, repositions, and every buff that lands on a minion.
`SimClient` never sends the sidecar a per-job worker count, so a 2000-trial
*guide* gets the same four-core fan-out as the fight of record.

The machine is an i9-14900K: 8 P-cores at 5.7–6.0 GHz exposed as CPUs `0–15`,
16 E-cores at 4.4 GHz as CPUs `16–31`, 62 GB RAM. This is not a shortage of CPU
in aggregate — 4 busy threads out of 32 — it is contention for the specific
cores the game's render thread wants.

There is a third burst nobody has complained about yet, found while reading the
path: `Pipeline.handle` matches `ev.CombatStart` and awaits `self._simulate()`
with no notion of whether the event is live or historical. The tailer reads each
session log from the top, so **`--replace` mid-session re-simulates every combat
in the session log**, serialized, at the full 8000 trials. CLAUDE.md's "~3s for
an 86MB log" is parse time and does not include this.

## Scope

Two changes, aimed squarely at gameplay smoothness:

- **A — scheduler isolation.** Tell the kernel the simulator is background work
  and keep it off the P-cores.
- **B — burst reduction.** Stop generating load at the moments that hurt, and
  stop generating it for work nobody is waiting on.

Plus the instrumentation to show either one worked, since the question this
spec answers ("did the game stop hitching") is not one the existing test suite
can see.

### Not in scope

- **Overlay and compositor work.** Throttling the 120ms partial-driven repaints
  and auditing whether the layer-shell surface forces a compositor pass on every
  game frame is real work with a plausible payoff, but it is speculative until
  there is frametime data. It gets its own spec, after the measurements here.
- **Pinning Hearthstone itself to the P-cores.** `scripts/steam-launch.sh` wraps
  `%command%` and could, but that is the game's business and `gamemode` is
  already installed doing adjacent work. Moving the simulator away is strictly
  better regardless of where the game lands.
- **Changing `sim_count` (8000) or `SHOP_DEBOUNCE` (0.30).** Both are levers of
  last resort. A and B should not need them; if they do, that is a finding.
- **Recovering the odds latency A costs.** See "What this does not improve" —
  it is accepted, not fixed.
- **Moving the simulator to a remote server.** Considered and rejected: there
  are 16 E-cores at 4.4 GHz nearly idle during play, so this is not a capacity
  problem and off-box capacity does not address it. Four threads out of 32 is
  ~12% utilisation; a hitch is one preempted frame, not saturation, so the fix
  is denying the simulator the cores the game wants rather than finding it more
  cores. A server would also add a network dependency during play where there
  is none today, put a round trip inside the 120ms partial stream that is what
  makes a number reach the HUD quickly, and require replicating the poison-board
  watchdog across a network boundary — all to remove the simulator only, leaving
  tailer, parser and overlay local regardless.

## A — Scheduler isolation

New module `bgtracker/sim/cpu.py`, consumed by `SimClient._ensure_proc`.

### Finding the efficiency cores

`efficiency_cpus() -> frozenset[int] | None`

Prefer `/sys/devices/system/cpu/types/*/cpulist`, which newer kernels populate
with `intel_atom` / `intel_core`. It is **absent on this machine's kernel**, so
the fallback is load-bearing rather than theoretical: read every
`cpu*/cpufreq/cpuinfo_max_freq`, group by value, and return the CPUs in the
lowest tier when there is more than one tier. Here that is three tiers —
4400000, 5700000, 6000000 — collapsing to `16–31`. (The two 6.0 GHz CPUs are
Turbo Boost Max favoured cores; grouping by exact frequency and taking only the
*lowest* tier keeps them on the performance side where they belong.)

Returns `None` when there is one tier — a non-hybrid CPU, where there is no
meaningful set of cores to exile the simulator to.

`cpu_capacity` is not usable for this: on x86 the kernel reports 1024 for every
CPU regardless of core type.

### Applying it

The sidecar is spawned with a `preexec_fn` that applies, in order:

1. `os.sched_setaffinity(0, efficiency_cpus())` — skipped when non-hybrid;
2. `os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))`;
3. `os.nice(19)`.

Each step is individually guarded. **The function must never raise**: an
exception inside `preexec_fn` is re-raised in the parent as a `SubprocessError`,
so a kernel that refuses one of these would take the sidecar down rather than
degrade. Failing to lower our own priority is not a reason to have no odds.

**Why `preexec_fn` and not `os.sched_setaffinity(proc.pid, …)` after spawn.**
Affinity and scheduling policy are per-*thread* on Linux, and inherited at
thread creation rather than applied retroactively to a process. The sidecar's
workers are created at module load — before the parent has even read the `ready`
line — and `server.mjs::retireWorker` spawns *more* of them during play when the
watchdog kills a hung one. Setting the mask before `exec` is the only point that
covers every thread, present and future, with no race and no walk of
`/proc/<pid>/task/`. `preexec_fn`'s documented hazard is threads in the parent,
and this parent is deliberately single-threaded (CLAUDE.md: "Don't add
threads") — so the one thing that makes it dangerous is a thing this codebase
already forbids.

**Why `SCHED_IDLE` and not merely `nice`.** The simulator already degrades
gracefully under starvation, and that is what licenses the aggressive setting:
`maxAcceptableDuration` cuts the run short and the sidecar pools whatever shards
finished, so scheduler pressure costs **sample size**, never correctness. A
starved run returns a wider `margin`, and `sims_run` records that it did.
`nice(19)` stays as the fallback for a kernel that refuses `sched_setscheduler`,
since `SCHED_IDLE` ignores nice entirely.

### Configuration

One new option, per the project's rule that a `Config` field and a `Setting`
are added together:

| Key | Type | Default | Channel |
|---|---|---|---|
| `sim_cpu_policy` | `"auto"` \| `"off"` | `"auto"` | `SIM_RESPAWN` |

`"auto"` applies the three steps above; `"off"` is today's behaviour, kept
because this is the setting somebody will want to toggle while bisecting a
performance complaint.

`SIM_RESPAWN` is the right channel because the mask is set at spawn and
genuinely cannot change without one. `SimClient.reconfigure_workers` generalises
to compare the CPU policy as well as the worker count and respawn if either
moved; the docstring's rationale for taking `_lock` first is unchanged and still
the load-bearing part.

## B — Burst reduction

### B1 — the shop forecast stops fanning out

`server.mjs::simulate` already accepts a per-job `workers` count and honours it
(`usable = max(1, min(pool.length, workers || pool.length))`). `SimClient`
simply never sends it. Add:

- `SimClient.simulate(..., workers: int | None = None)`, passed through to the
  payload;
- a `shop_workers` attribute on `SimClient`, set from config in `apply_config`
  (channel `SIM`, free — read fresh per call, exactly as `sims` already is);
- `Pipeline._shop_forecast` passes `workers=self.sim.shop_workers`.

| Key | Type | Default | Channel |
|---|---|---|---|
| `sim_shop_workers` | int, 0–8 (0 = all) | `1` | `SIM` |

Peak parallel load during the recruit phase drops four-fold. The shop number
arrives in roughly 400ms instead of 100ms, which is the cheap side of the trade
for something the overlay already labels as a guess against a board of stated
age.

**The consequence that has to be handled.** A longer shop job widens the window
in which a *cancelled* shop forecast is still running when `CombatStart`
dispatches the real one. `server.mjs`'s watchdog comment calls this out
precisely:

> Assumes one job in flight at a time — which SimClient guarantees by holding
> its request lock across each call. If concurrent jobs ever shared workers,
> retiring a stuck worker here would silently kill another job's shards.

The lock guarantees it *except* on cancellation: `_cancel_shop_forecast` raises
`CancelledError` at the `await`, which releases `_lock` while the sidecar job
keeps running. That is latent today and B1 makes the window several times wider,
so it gets fixed here rather than left:

- shop jobs dispatch from the **end** of the pool, combat jobs from the start;
- a combat job's `usable` excludes the shop workers whenever that leaves it at
  least one.

With the default four workers and `sim_shop_workers = 1` that is 3 for combat,
1 for shop, and the watchdog's one-job-at-a-time assumption becomes true by
construction rather than by the lock alone.

Two edge cases, pinned so the implementation does not have to guess:

- **`sim_shop_workers = 0`** means "all workers", which is the pre-B1 behaviour
  and cannot reserve anything. The pool is shared and the overlap stays as
  latent as it is today. The option is kept for bisecting, not recommended.
- **A pool of one** cannot reserve either. Combat takes it, and the shop job
  shares it.

This costs the combat forecast about a third of its throughput, and that stacks
with the move to E-cores. **The default `sim_workers` is therefore re-measured,
not guessed** — see "Open decision".

### B2 — catch-up stops re-simulating history

`Pipeline.handle` gains `historical: bool = False`. When set, `_simulate`
returns `None` without touching the sidecar. The live tail loop passes
`historical=True` for the first read of a session — the one that drains an
already-existing file — and `False` for every read after it.

**The data hazard this exposes.** `HistoryDB.record_combat` is
`INSERT OR REPLACE` against `UNIQUE (game_id, turn)`, and `start_game` resumes
an existing row by `log_id`. So a catch-up today **overwrites** the stored
combat rows. That is harmless while it re-predicts them with an equally valid
number, and destructive the moment B2 stops it predicting: every combat in the
session would have its recorded prediction blanked, and the calibration table is
built from exactly those columns.

The write becomes an upsert that cannot lose data:

```sql
INSERT INTO combats (...) VALUES (...)
ON CONFLICT (game_id, turn) DO UPDATE SET
    predicted_win        = COALESCE(excluded.predicted_win, predicted_win),
    predicted_tie        = COALESCE(excluded.predicted_tie, predicted_tie),
    predicted_loss       = COALESCE(excluded.predicted_loss, predicted_loss),
    sims_run             = COALESCE(excluded.sims_run, sims_run),
    sim_ms               = COALESCE(excluded.sim_ms, sim_ms),
    predicted_lost_lethal = COALESCE(excluded.predicted_lost_lethal, predicted_lost_lethal),
    outcome              = COALESCE(excluded.outcome, outcome),
    my_board             = excluded.my_board,
    opp_board            = excluded.opp_board,
    opponent_is_ghost    = excluded.opponent_is_ghost
```

Boards and the ghost flag are projections of the same log and overwrite freely;
the prediction columns and the outcome are the ones a null must never blank.
This closes the hole for every path that re-records without a prediction, not
only for catch-up.

The narrow alternative — have `historical=True` skip `record_combat` as well as
the simulation — was rejected. Catch-up writing earns its keep in the case where
the game was never tracked live (the tracker was started mid-session, or a fresh
install found an existing session log): those combats' boards and outcomes are
the only record that will ever exist, and they feed `resim`'s corpus with or
without a prediction attached. Skipping the write discards that to fix a problem
that belongs to the write.

**The same hazard is already corrupting data, today.** `end_game` does
`SET ended_at=?` with `_now()`, so re-recording a finished game moves its end
time to whenever it was replayed. 14 games in the author's database claim
durations over three hours:

```
id  started_at                 ended_at                   placement
15  2026-07-22T23:06:47+00:00  2026-07-23T21:48:00+00:00  4
16  2026-07-22T23:40:29+00:00  2026-07-23T21:48:43+00:00  2
17  2026-07-23T00:14:34+00:00  2026-07-23T21:49:00+00:00  4
```

Those `ended_at` values are seconds apart on one evening — a replay pass
marching through and stamping each row. It is harmless only by luck: `review.py`
groups periods on `started_at`, which `start_game` preserves, and nothing reads
`ended_at` at all. Fixed in the same breath, for the same reason:
`SET ended_at = COALESCE(ended_at, ?)`. A game ends once, and the first time it
was seen to end is the true one.

The 14 existing rows are left as they are. There is no source of truth to
repair them from — the real end times are gone — and a `_USER_VERSION` fixup
that guessed would be worse than a value nothing reads.

No schema change, so no `_ADDED_COMBAT_COLUMNS` entry and no `_USER_VERSION`
bump — these are changes to two statements, not to the tables.

**Stated cost.** Restarting *into* a live combat loses that one fight's odds;
the display recovers at the next `ShopReady`. The refinement — re-simulate the
final `CombatStart` of the catch-up batch when no `CombatEnd` followed it — is
deliberately not taken. If restarting mid-combat turns out to be common enough
to notice, it is a small follow-up.

## Measurement

Two questions, two instruments, and only one of them is new.

**"Did the game stop hitching?"** Nothing in the repo can answer this. Add
`scripts/frametime-capture.sh`, wrapping MangoHud's logging
(`MANGOHUD_CONFIG=output_folder=…,log_duration=…`; `mangohud` is already
installed), plus a small reader that reports mean, 1% low and 0.1% low frame
times from the CSV. Capture several recruit phases and shop→combat transitions
before and after each of A and B. The 0.1% low is the number that corresponds to
"hitch"; a mean FPS figure will hide exactly the thing being fixed.

**"Did the odds get worse?"** Already built, and needs only extending.
`sims_run` is recorded per combat and is a flat 8000 across all 589 rows today,
so any truncation introduced by A or B shows up in the database without anybody
instrumenting anything; `resim` already prints a count of runs that came back
under 4000 trials. Extend `resim`'s `Row` with `sim_ms` and print the wall-time
distribution, which makes the existing regression harness double as the
throughput bench — it already replays stored boards through the live sidecar
serially, which is the benchmark loop.

**Order of work.** Bench + capture → A → measure → B → measure → set defaults
from the numbers. The measurement steps are not a formality at the end; the open
decision below is resolved by them.

## Open decision

**The worker count and split.** The combat forecast ends up on 3 workers instead
of 4, running on 4.4 GHz cores instead of 5.7–6.0. Those stack. The current
default of 4 workers was chosen against a measurement in `server.mjs` — "9.2k →
34k sims/s going 1 → 4 workers, but only 38k at 8" — taken on P-cores at normal
priority, which is no longer the configuration being run. Each worker costs
~350 MB, so 6–8 workers is 2.1–2.8 GB against 62 GB of RAM: affordable if it
buys the throughput back.

The bench sets this. It is not resolved in this spec because it should not be
resolved by argument.

## Testing

| Area | Test |
|---|---|
| E/P classification | `tests/test_sim_cpu.py` — synthetic sysfs trees: `types/` present, `types/` absent with three frequency tiers (this machine), single tier (non-hybrid → `None`), missing `cpufreq` entirely |
| `preexec_fn` | never raises, whatever `sched_setscheduler`/`nice`/`setaffinity` do |
| Settings | `tests/test_settings_schema.py` already fails if a `Config` field, its `Setting`, and `config.example.toml` disagree — both new options are covered by adding them properly |
| History | re-recording a combat with `prediction=None` leaves the stored prediction intact; re-ending a finished game leaves its original `ended_at` intact |
| Pipeline | `handle(..., historical=True)` issues no simulate call; `historical=False` still does |
| Sidecar | a job with `workers=1` runs on one worker and pools identically to the same trial count spread across four |

Whole suite (~320 tests, ~23s) after every change, per the project rule. `resim`
after A and after B, judged on `sims_run` truncation and Brier direction rather
than mean drift — A and B are not supposed to move predictions at all, so any
drift beyond Monte Carlo noise is a finding.

## What this does not improve

Stated plainly, because the scope invites the assumption:

- **The overlay does not get faster.** It runs at normal priority on the
  P-cores, never blocks on the simulator, and is untouched by A and B.
- **The odds get slower.** Combat forecasts from a mean of 387ms toward
  600–800ms, shop forecasts from ~100ms to ~400ms. Partials still put a number
  on screen in ~120ms, so what changes is how fast it firms up, not when it
  first appears. This is the trade, deliberately taken: latency and sample size
  are what the simulator can give up without giving up correctness.
- **The one tracker-side win is B2.** A mid-session `--replace` currently runs
  a serialized 8000-trial simulation for every combat already in the session
  log before the overlay is current. Skipping them takes that from potentially
  tens of seconds down to roughly the ~3s the parse costs.
