# Next session: finish validating the simulator CPU isolation

Written 2026-07-29, immediately after merging `498f817`. Paste the block below into
a fresh session. Everything it needs beyond that is in
`docs/superpowers/specs/2026-07-28-sim-cpu-isolation-design.md`, the Measurements
section of `docs/superpowers/plans/2026-07-28-sim-cpu-isolation.md`, and `CLAUDE.md`.

The ordering is deliberate: **step 1 decides whether the rest is worth doing.** The
branch shipped a fix for a problem that was diagnosed by inference and never
measured, so validating it comes before extending it.

---

```
The simulator CPU-isolation work merged to master as 498f817 on 2026-07-29 but its
justifying measurement was never taken. See the memories
`sim-cpu-isolation-shipped-unvalidated` and `sidecar-orphans-survive-their-parent`,
plus docs/superpowers/plans/2026-07-28-sim-cpu-isolation.md (Measurements section)
and the spec beside it. Four things, in this order:

1. VALIDATE THE CHANGE FIRST — do not write code before this.
   The claim that the tracker caused my recruit-phase and shop→combat stutters is
   inference, not measurement: no preempted frame was ever observed. Walk me
   through capturing MangoHud frametimes, including a tracker-stopped control, and
   read them with scripts/frametime-report.py judged on the 0.1% low.
   If the hitches did NOT improve, STOP and tell me the mechanism isn't what the
   design assumed. Do not start tuning or extending the fix — reassess it. That
   outcome is a legitimate result, not a failure to work around.

2. Bench sim_workers, on a quiet machine only (no game running, no other tracker).
   The combat forecast now runs on 3 workers instead of 4, on 4.4 GHz E-cores
   instead of 5.7-6.0 GHz P-cores, and nobody has measured whether raising the
   count recovers that. Sweep with
   BGTRACKER_SIM_WORKERS=<n> .venv/bin/python -m bgtracker resim 40
   and judge wall time AND sims_run together. Every pre-branch combat is a flat
   8000, so any row below 8000 means the scheduler starved a run into truncation —
   that finding outranks any speed number and means sim_cpu_policy should go back
   to "off". Set the default from the numbers or leave it and say the numbers
   didn't justify a change; don't raise it by argument.

3. Fix the orphaned sidecars. sim/client.py's docstring claims the sidecar is
   "killed with the parent" and it isn't — five orphans were holding 2.4 GB. The
   natural fix is prctl(PR_SET_PDEATHSIG, SIGTERM) in the preexec_fn that already
   exists in bgtracker/sim/cpu.py, which runs in the child between fork and exec.
   Respect the constraint documented there: that applier must never raise (an
   exception becomes a SubprocessError and takes the sidecar down) and must stay
   thin syscall wrappers. Spec-first per CLAUDE.md if there's a decision in it.

4. Then the two deferred items from the code review, which collapse into one piece
   of work: a stub-worker harness for sidecar/server.mjs (an env-overridable
   WORKER_FILE pointing at a fake worker that sleeps on command) so the
   cancellation-overlap and worker-crash paths become testable deterministically
   rather than by timing. That unblocks narrowing failAllFor, which currently
   fails EVERY in-flight job when any worker dies — so a dying shop worker still
   costs a concurrent combat forecast its odds. Also clear backgroundJobId in
   failAllFor; it's the one exit path that doesn't.

Also still open, unrelated to the above: my uncommitted work on
bgtracker/data/art.py and tests/test_art.py, and master + sim-cpu-isolation are
both local-only, never pushed to github.com/1MrJohan/hearthlinux.
```

---

## Why the prompt is shaped this way

**Step 1 carries an explicit stop condition** because the likely failure mode is an
agent treating "validate the fix" as a formality and sliding into tuning. If the
frametimes did not move, the correct response is to doubt the diagnosis, not to add
scheduling knobs.

**Step 2 ranks `sims_run` above wall time** because a bench that reads only
throughput will happily recommend a configuration that is quietly truncating
simulations — trading odds accuracy for speed, which is the opposite of the trade
the spec agreed to.

**The last paragraph exists** so a fresh session does not mistake pre-existing
uncommitted work for its own mess and "tidy" it away.

## Status at the time of writing

- `498f817` on `master` — merge of `sim-cpu-isolation`, 372 passed / 1 skipped.
- Change A verified against a live sidecar: all nine threads at
  `Cpus_allowed_list: 16-31`, `SCHED_IDLE`, nice 19.
- Accuracy verified: `resim 40` → mean drift 0.14 points, 0 rows moved ≥1 point,
  no truncation.
- Frametime baseline: **not captured.**
- `sim_workers` bench: **not run.**
