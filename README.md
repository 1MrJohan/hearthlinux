# hs-bg-tracker

Linux-native Hearthstone Battlegrounds tracker: live board state, combat
win/tie/loss odds (Firestone's actual simulator), opponent-board memory,
and local match history. Log-driven — no game-memory reading, no Overwolf.

Works with Hearthstone running under Wine/Proton (Lutris, Bottles, or Steam);
the tracker auto-discovers the install across prefix layouts.

## Setup

```bash
# system deps (Arch/CachyOS)
sudo pacman -S --needed python-gobject gtk4 gtk4-layer-shell python-xlib nodejs

git clone <this repo> && cd hs-bg-tracker
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
(cd sidecar && npm install)
```

First run writes `log.config` into the Hearthstone Wine prefix to enable
Power/Bob logging — **restart Hearthstone once** after that.

Run Hearthstone in **borderless windowed** mode (exclusive fullscreen can
cover the overlay).

## Usage

```bash
.venv/bin/python -m bgtracker                 # live tracking, console output
.venv/bin/python -m bgtracker --overlay       # live tracking + on-screen overlay
.venv/bin/python -m bgtracker --overlay --replace  # …restarting any instance already running
.venv/bin/python -m bgtracker --overlay --demo  # overlay with fake data, no game needed
.venv/bin/python -m bgtracker stats           # match history + sim calibration
.venv/bin/python -m bgtracker history         # match history & MMR review window
.venv/bin/python -m bgtracker settings        # settings window, standalone
.venv/bin/python -m bgtracker doctor          # diagnose the pipeline end to end
.venv/bin/python -m bgtracker mmr 8421        # record a rating reading by hand
.venv/bin/python -m bgtracker resim 40        # re-simulate stored combats (regression harness)
.venv/bin/python -m bgtracker --replay FILE --odds   # replay a saved Power.log
.venv/bin/python -m bgtracker --no-names      # skip the card-name DB download
```

### Launching with the game

`scripts/steam-launch.sh` runs the tracker for exactly as long as Hearthstone
is up. Point Steam at it — *Properties > Launch Options*:

```
/path/to/hs-bg-tracker/scripts/steam-launch.sh %command%
```

Or run it standalone with no arguments (Lutris, Bottles, hand-launched):
`scripts/steam-launch.sh &`.

It watches for the `Hearthstone.exe` process rather than tying itself to the
wrapper, because the wrapper's lifetime is the whole Battle.net session — it
spans time at the launcher and outlives quitting the game. Quitting and
reopening within one session works. Each start uses `--replace`, so a game
always gets a tracker running current code. Activity is logged to
`~/.cache/hs-bg-tracker/launch.log`.

Detection matches the process *name*, never the command line — a cmdline match
also hits any shell or launcher that merely mentions the game. If your build
reports a different name (check with `pgrep -x Hearthstone.exe` while in a
match), set `BGTRACKER_GAME_PROC` to the right one.

A systemd user service is the tempting alternative and the awkward one: the
overlay needs `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR` and `DISPLAY` (the hover
poll talks to XWayland) plus the `gtk4-layer-shell` `LD_PRELOAD`. A child of
the game launch inherits all of that; a user unit does not.

Restarting is cheap and safe mid-game: the tailer reads each session log from
the top in small, cooperative slices and rebuilds the current game's state
without re-simulating old combats. A measured 163MB session catches up in about
4.5s while yielding to the overlay between slices. You never need to quit
Hearthstone or wait for a match to end. Launches are NON_UNIQUE so a stale
instance can't swallow a new one — which also means an old process keeps
running the code it started with, so `--replace` (or the startup warning naming
the old pid) is how you make sure you're looking at your latest changes.

Overlay smoke test (run before first `--overlay`, see checklist inside):

```bash
.venv/bin/python scripts/layer-shell-probe.py
```

Capture a finished game's log as a test fixture:

```bash
scripts/capture-fixture.sh normal-game
```

## Architecture

```
Hearthstone (Wine/Proton) → Power.log → tailer → hslog exporter → typed events
                                                                     │
                          ┌─────────────┬────────────────────────────┤
                    GTK4 overlay   history.db (SQLite)      combat simulator
                  (gtk4-layer-shell)  + stats/calibration   (Node sidecar running
                                                             @firestone-hs/simulate-bgs-battle)
```

- **Phase detection**: `BOARD_VISUAL_STATE` on the GAME entity (1 = shop,
  2 = combat); both boards are snapshotted at the 1→2 transition.
  Power.log carries two streams of these. `GameState` is authoritative and is
  what hslog parses, but it leaves combat ~1s after entering it — that is just
  how long the engine takes to resolve the fight. `PowerTaskList` is the
  client's animation queue, and its flip back to shop is when the player
  actually stops watching (median 23s later, up to 48s). Snapshots and history
  follow `GameState`; anything on screen follows the `ShopReady` event, which
  is raised from the `PowerTaskList` marker.
- **Odds accuracy**: the mapper sends stats, keywords, tavern tier, hero
  HP/armor, attached enchantments, the friendly hand, equipped trinkets, and
  the lobby-wide tribe buffs (`globalInfo`). Still unmapped: `validTribes`,
  anomalies, and quests — those degrade *accuracy*, not correctness. Hero
  powers are excluded **on purpose**: the simulator needs per-power `info`
  state, and sending a bare id makes it misapply even non-combat powers
  (verified swinging a 16% combat to 0%). `bgtracker stats` prints a
  calibration table (predicted vs actual win rate per bucket) — that table is
  the evidence for what to map next, and `bgtracker resim` replays stored
  combats through the current mapper to show what a change moved.
- Card names/data from HearthstoneJSON, cached in `~/.cache/hs-bg-tracker/`.
  Card data and art are Blizzard's copyright; personal use only.

### Overlay

The overlay uses the "Dark Oak" skin (see `design_handoff_overlay_redesign/`):
a HUD with turn medallion and win/tie/loss bar, tavern buffs, a next-opponent
panel carrying live recruit-phase odds against that player's last-seen board,
and a leaderboard rail of hero portraits whose rows open a scout popout on
hover. Reviewing a board is the scout popout's job — an enemy-board panel
exists but is dormant, because during a fight it only duplicated what the game
was already showing. Panels are individually draggable —
turn on **Layout mode** in settings (or hit the ⚙ above the HUD), arrange them,
then hit **Lock layout**.

### Settings

A ⚙ sits just above the HUD's top-right corner — the only part of the overlay
that takes a click rather than passing it through to Hearthstone. It opens a
settings window, which also runs on its own:

```bash
bgtracker settings
```

Everything applies immediately, including overlay scale, monitor, and simulator
worker count; nothing needs a restart. The window also carries the **Debug**
page — log level, a rotating log file, live tracker status, the same checks
`bgtracker doctor` runs, and a one-click diagnostics bundle — and a **Reset**
page for panel layout, hover calibration, all settings, and (with a typed
confirmation and an offered backup) the match history.

`config.example.toml` documents every option and is generated from the schema in
`bgtracker/settings.py`, so it cannot drift from what the tracker accepts.
Values are validated on load, so a hand-edited file that is merely out of range
is clamped rather than refusing to start.

### Match history

```bash
bgtracker history
```

An MMR trend chart, per-day/week/month results, and a drill-down into each
game's combats with what was predicted against what happened. Ratings are
manual snapshots (`bgtracker mmr <value>`), so every derived number says only
what the readings support: a period's net is the difference between the last
readings either side of it, and a single game gets a delta only when it is the
only game between two consecutive readings.

Panel sizes derive from the monitor by default, so the HUD covers the same
share of screen as the design mock (1080p ~1.53, 1440p ~2.04, 4K capped at
3.0); set `overlay_scale` to override. Panels that outgrow their saved
position — a seven-minion board, say — are nudged back on-screen at startup
without rewriting your layout.

Styling and design tokens live in `bgtracker/overlay/theme.py`. Cinzel and
Alegreya Sans (both SIL OFL) ship in `bgtracker/assets/fonts/` and are
registered at runtime with `Pango.FontMap.add_font_file` — no system font
install needed. Registration must happen before any font lookup, since Pango
caches the face it resolves per description.

Hovering never captures the pointer: the rail and the game's own leaderboard
are both detected by polling the X11 cursor, so the overlay stays
click-through and Blizzard's native board preview keeps working.

`--overlay --demo` renders every state (hero select, combat, shop, golden
minion, eliminated player) without a live match — use it when changing the
skin.

## Development

```bash
.venv/bin/python -m pytest tests/       # includes a real-sidecar integration test
```

Tests run against synthetic Power.log content (`tests/synthetic.py`) plus any
captured fixtures in `tests/fixtures/`.
