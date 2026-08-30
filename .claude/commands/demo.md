---
description: Launch the overlay in demo mode to eyeball a skin or layout change
---

Launch the overlay against fake data, taking over any instance already running:

```bash
.venv/bin/python -m bgtracker --overlay --demo --replace
```

Run it in the background — it is a GUI that stays up until stopped.

Notes that matter when reading the result:

- **Don't add `LD_PRELOAD` yourself.** `__main__.py` re-execs once with it set, because
  `gtk4-layer-shell` has to link before libwayland. A hand-set preload is a sign
  something else is wrong.
- Demo drives the **real** `on_event` wiring, so what you see is what live play renders.
  It cycles every state worth checking: hero select, combat, shop, a golden minion, and
  an eliminated player on the rail.
- This is the check for anything touching `overlay/theme.py`, panel layout, or widget
  construction — the design tokens in `theme.py` are the source of truth and
  `design_handoff_overlay_redesign/README.md` is the approved handoff to compare against.
- If it comes up blank or unstyled, suspect font registration order:
  `theme.register_fonts()` must run before any widget is constructed, or Pango pins the
  overlay to the fallback face for the life of the process.
- On a machine that has never run the overlay, run `.venv/bin/python
  scripts/layer-shell-probe.py` first.
