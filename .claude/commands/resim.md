---
description: Run the sim regression harness and judge the result the right way
argument-hint: "[number of recent combats, default 40]"
---

Run the odds regression harness over stored combats:

```bash
.venv/bin/python -m bgtracker resim $ARGUMENTS
```

If no count was given, use 40.

This replays combats from `history.db` through the *current* mapper and simulator and
prints predicted-then vs predicted-now vs actual, worst disagreement first. Report the
result with these rules — they are the whole reason this command exists, and every one
of them has been got wrong before:

1. **Brier direction is the verdict, not mean drift.** A change that moves the mean by a
   couple of points but *lowers* Brier over the decided combats is an accuracy gain and
   stays. A change that raises Brier is a regression to reject, however small the drift.
   The 1.1.721→1.1.724 sim bump is the worked example: mean moved ~2.5 points, Brier went
   0.152→0.138 over 124 decided combats, so it stuck.
2. **Mean drift under ~0.2 points is Monte Carlo noise**, not a finding. Do not report it
   as a change.
3. **An occasional single-combat error is expected and honest.** A specific board can send
   a Firestone worker non-terminating; the sidecar's per-job watchdog returns what the
   finished shards gathered and respawns the worker, so a poison board costs only its own
   fight. One or two of these in a long run is the pool healing, not a cascade — say so
   rather than treating it as a failure. Many in a row is a real problem.
4. **Ghost fights are excluded from calibration** via `combats.opponent_is_ghost`, not via
   the outcome. If the numbers look odd, check that flag before theorising.

Then state plainly whether the change should stay, quoting the actual numbers.
