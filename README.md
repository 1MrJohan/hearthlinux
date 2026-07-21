# hs-bg-tracker

Linux-native Hearthstone Battlegrounds tracker: live board state, combat
win/tie/loss odds (Firestone's actual simulator), opponent-board memory,
and local match history. Log-driven — no game-memory reading, no Overwolf.

Works with Hearthstone running under Wine/Proton (Lutris, Bottles, or Steam);
the tracker auto-discovers the install across prefix layouts.

## Setup

```bash
# system deps (Arch/CachyOS)
sudo pacman -S --needed python-gobject gtk4 gtk4-layer-shell nodejs

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
.venv/bin/python -m bgtracker stats           # match history + sim calibration
.venv/bin/python -m bgtracker --replay FILE --odds   # replay a saved Power.log
```

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
- **Odds accuracy**: the mapper currently covers stats/keywords/tier
  ("Tier 0"). `bgtracker stats` prints a calibration table (predicted vs
  actual win rate) that shows when mapping gaps matter; enchantment, hero
  power, and quest mapping land next based on it.
- Card names/data from HearthstoneJSON, cached in `~/.cache/hs-bg-tracker/`.
  Card data and art are Blizzard's copyright; personal use only.

## Development

```bash
.venv/bin/python -m pytest tests/       # includes a real-sidecar integration test
```

Tests run against synthetic Power.log content (`tests/synthetic.py`) plus any
captured fixtures in `tests/fixtures/`.
