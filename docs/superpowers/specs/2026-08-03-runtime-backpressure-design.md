# Runtime backpressure for log catch-up and overlay rendering

Date: 2026-08-03

## Problem

The simulator is already isolated from gameplay: the Node process inherits
efficiency-core affinity, `SCHED_IDLE`, and nice 19; recruit-phase simulations
have their own worker lane; and historical combats are not re-simulated.  A
review of the remaining runtime path found a separate source of bursty work in
the Python/GTK process.

The live tail loop currently reads every byte between its offset and EOF into
one bytes object, splits and decodes the whole result, parses every line, and
only then returns control to the shared GTK/asyncio loop.  On a recent captured
session this meant:

```text
Power.log        163,208,352 bytes
lines              1,192,044
read                    0.325 s
parse                   6.772 s
total                   7.097 s
peak RSS              641,936 KiB
```

That is most visible after `--replace` during a live game: the overlay is
unresponsive for the whole catch-up and one normal-priority Python core runs
continuously alongside Hearthstone.  The same unbounded allocation makes a
large session unnecessarily expensive even when the tracker starts at the
menu.

A `cProfile` run over a second 73.7 MB captured session found that much of the
parse cost was not hslog itself.  `BGExporter._advance()` asked every completed
top-level packet to re-project display state:

```text
7356 hero checks / 4.64 cumulative seconds
7356 buff checks / 3.65 cumulative seconds
1207 board projections / 1.55 cumulative seconds
```

Most packets cannot change those views, and the UI cannot display thousands of
intermediate states from one tail read.  Combat snapshots are different: the
opponent can materialize just after `BOARD_VISUAL_STATE` flips to combat, and
waiting until the end of a batch could capture combat-mutated stats.  Their
packet boundary is authoritative and must not move.

Finally, every typed event calls `OverlayApp._flush()` immediately.  A catch-up
batch can therefore perform hundreds of GTK setter/layout passes without ever
giving GTK an opportunity to draw an intermediate result.

## Goals

- Bound the amount of log input and Python work performed before yielding the
  shared GTK/asyncio loop.
- Preserve the exact historical/live boundary even when the initial file is
  drained over many reads.
- Stop full entity-tree scans that cannot produce a user-visible intermediate
  state.
- Collapse synchronous event bursts to one GTK diff/render pass per main-loop
  turn.
- Keep combat snapshots, event order, history upserts, and simulator inputs
  mathematically unchanged.

## Non-goals

- Changing simulation trial counts, timeouts, worker counts, CPU affinity, or
  the Firestone package.  Those controls already have their own evidence and
  regression workflow.
- Replacing polling with inotify.  Once idle, the loop stats one file every two
  seconds; that is not where the measured cost is.
- Adding threads.  GTK and asyncio deliberately share one GLib loop, and the
  parser's mutable hslog tree is not a safe thread boundary.
- Claiming a frametime improvement without a MangoHud before/after capture.
  Parser timings and allocation peaks can be measured here; gameplay 0.1% lows
  require a real display and game.

## Design

### 1. Bounded tail reads with a stable catch-up high-water mark

`Tailer.read_new_lines()` gains an optional byte limit.  The live loop uses a
256 KiB limit; direct callers retain the existing unlimited default.

At construction, the tailer records the file size that already exists as its
catch-up high-water mark.  Every batch beginning below that mark is historical,
and a read is capped at the mark so one batch never mixes historical and live
bytes.  If the file does not exist or is empty at construction, the mark is
zero and its first later lines are live, preserving the existing invariant.

The tailer exposes per-read state:

- `last_read_historical`: the returned batch came from below the high-water
  mark;
- `catchup_completed`: this read reached that mark;
- `backlogged`: unread bytes remain at the size observed for this read.

Truncation establishes a new high-water mark at the rewritten file's current
size.  Replaying that content as historical prevents duplicate old combats
from being simulated after a log rotation or rewrite; the current code resets
to offset zero but incorrectly treats those lines as live once its one-shot
`catching_up` flag has cleared.

While `backlogged` is true, the live loop skips the configured poll delay and
instead sleeps for 1 ms.  This keeps draining promptly while returning control
to GLib between slices and creating a small scheduling gap for the game.  Once
caught up, the existing active/idle polling policy is unchanged.

### 2. Historical shop work is suppressed until catch-up completes

Chunking makes catch-up last across main-loop turns.  Without another guard,
the 300 ms recruit forecast debounce can expire halfway through and simulate an
obsolete intermediate board.

`Pipeline.handle(..., historical=True)` therefore continues rebuilding the
current shop board, next-opponent identity, and opponent memory, but does not
schedule a shop simulation.  `Pipeline.finish_catchup()` schedules at most one
forecast from the final reconstructed state.  `CombatStart` and `GameEnd` clear
the retained recruit board, so a high-water mark reached mid-combat or after a
finished game cannot restart work against a stale board.  Combat simulations
remain suppressed exactly as before.

### 3. Coalesce display projections, preserve combat projection timing

`BGExporter._advance()` keeps these operations inside the per-packet loop:

- exporting the packet into hslog's entity tree;
- emitting `GameStart` at the first valid game entity;
- hero detection until the real hero is emitted;
- `maybe_emit_combat()`, because its snapshot boundary is authoritative.

Standings, buffs/economy, and the recruit-phase board are projected once after
all currently complete packets have advanced.  These are retained display
state, already equality-deduplicated, and no durable consumer depends on every
intermediate value inside a single tail read.  Live mode publishes them at most
once per 250 ms poll; catch-up publishes them at most once per 256 KiB slice.

Once a non-placeholder hero has emitted, `maybe_emit_hero()` returns before
scanning the entity tree.  A Battlegrounds player picks one hero per game; the
existing “re-emit on change” path was never consumed and charged a full hero
scan to every later packet.

### 4. One GTK render per main-loop turn

`OverlayApp.on_event()` continues updating `OverlayState` synchronously so no
event or ordering information is lost.  In the real application it schedules
one `GLib.idle_add()` flush instead of rendering immediately.  Further events
before that callback runs only mutate the retained state; the already-pending
source renders their final diff.

Explicit rebuilds still call `_flush()` immediately, and lightweight test
instances keep immediate flushing unless deferred mode is enabled.  Simulator
partials arrive over separate loop turns, so the existing progressive forecast
remains progressive rather than collapsing to the final value.

### 5. No perpetual overlay repaint while combat is idle

The combat medallion's `turnpulse` CSS animation runs indefinitely for the
20–45 seconds that the fight remains visible.  Even with no event or widget
change, an infinite animation keeps GTK's frame clock active and repaints part
of the transparent layer-shell surface on compositor frames.

The `.medallion.combat` state keeps its brighter gold glow but makes it static.
There are no other intentional overlay animations, so between typed events and
pointer transitions the surface can remain unchanged.  This trades a small
decorative pulse for a hard event-driven rendering invariant; the phase title,
combat class, odds, and all interaction remain unchanged.

## Correctness and regression evidence

Tests cover:

- byte-limited reads across partial line boundaries;
- an existing file remaining historical across every slice;
- a file created after the tailer remaining live;
- truncation being historical and completing once;
- one final shop simulation, rather than intermediate simulations, after
  chunked catch-up;
- combat snapshot contents and event sequence staying unchanged;
- multiple standings, buff, and shop mutations in one feed producing the final
  retained event once;
- multiple overlay events scheduling one idle render and rendering the latest
  state.
- the overlay stylesheet containing no animation declarations or keyframes,
  while retaining a distinct static combat style.

The full suite remains required.  The before/after parser benchmark uses the
same captured 163.2 MB session and reports wall time, event count, and peak RSS.
The 73.7 MB profile is repeated to confirm the removed scans no longer dominate.

Measured result on the 163.2 MB / 1,192,044-line session:

```text
                         before       after       change
parse time                6.772s      3.569s       -47%
wall time                 7.097s      4.545s       -36% (includes 623 1ms yields)
peak RSS             641,936 KiB 124,840 KiB       -81%
typed events                1775         699        -61%
```

Across the 623 bounded parser slices, the median uninterrupted parse burst was
5.45ms, p95 14.27ms, p99 23.27ms, and the maximum 46.94ms.  The old path was
one uninterrupted 6.77-second parse burst.

On the separate 73.7 MB captured session, bulk and 256 KiB parsing produced an
identical sequence of all 119 correctness-critical events: 23 combat starts,
21 combat ends, 2 game starts/ends, 2 hero picks, 46 opponent announcements,
and 23 turn changes.  Every combat snapshot compared equal.  Final standings
and buff state also matched; intermediate retained display events were the only
intentional difference.

## Manual validation still required

The code can demonstrate shorter bursts and lower peak memory, but only a live
MangoHud capture can establish that Hearthstone's slow-frame tail improved.
Capture the same recruit and shop-to-combat sequence with the tracker stopped
and running, then compare `scripts/frametime-report.py` output, prioritising the
0.1% low.  `--overlay --demo` is also required to inspect the coalesced GTK path
on a display.
