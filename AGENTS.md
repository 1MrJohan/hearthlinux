# AGENTS.md

This file applies to the entire repository. It is the operating guide for coding
agents working on `hs-bg-tracker`.

## Project in one minute

`hs-bg-tracker` is a Linux-native Hearthstone Battlegrounds tracker and GTK4
overlay. It reconstructs game state exclusively from Hearthstone's `Power.log`,
stores local match history in SQLite, and asks a Node sidecar running Firestone's
simulator for combat odds.

Two constraints shape the project:

- It is log-driven. Do not add game-memory reads, injection, Overwolf integration,
  or guesses presented as observed facts. If the log cannot reveal something,
  model that uncertainty honestly.
- It is Linux/Wine-native. Hearthstone runs through Wine or Proton; the overlay is
  GTK4 plus `gtk4-layer-shell` on Wayland, while hover detection polls XWayland via
  X11 without capturing the pointer.

For setup and user-facing behavior, read `README.md`. For detailed domain facts,
bug history, and explanations of the invariants summarized below, read
`CLAUDE.md` before making a non-trivial change. Design decisions live in
`docs/superpowers/specs/`; the approved overlay design is documented in
`design_handoff_overlay_redesign/README.md`. When prose conflicts with current
code, regression tests, or captured-log evidence, treat the evidence as authoritative
and update the affected documentation as part of the change.

## Working agreement

1. Inspect `git status` before editing. Preserve all existing changes and untracked
   files; they belong to the user unless the task explicitly says otherwise.
2. Trace a change through the full data path and find its existing tests before
   editing. Prefer the smallest coherent change that preserves layer boundaries.
3. For work involving a real product or architecture decision, write a design spec
   first at `docs/superpowers/specs/YYYY-MM-DD-<slug>-design.md`. A small fix whose
   rationale fits in a commit message does not need a spec.
4. Do not create commits, branches, or merge commits unless the user asks. If commits
   are requested, keep a design spec in its own earlier commit, use a feature branch
   for feature work, merge with `--no-ff`, and make commit bodies explain the failure,
   rationale, and evidence rather than restating the diff.
5. Add or update regression tests with behavior changes. Synthetic `Power.log`
   fragments belong in `tests/synthetic.py`; reusable captured games belong in
   `tests/fixtures/` and can be produced with `scripts/capture-fixture.sh`.
6. Run the full suite before calling any change complete. Report skipped tests and
   any manual or environment-dependent check that could not be run.

Do not write runtime state beside the source tree. XDG paths are resolved in
`bgtracker/config.py`:

- config and panel positions: `~/.config/hs-bg-tracker/config.toml`
- durable history: `~/.local/share/hs-bg-tracker/history.db`
- card data, art, and logs: `~/.cache/hs-bg-tracker/`

Treat `history.db` as user data. Caches are disposable; history is not.

## Setup and commands

Python 3.12 or newer is required. The virtual environment must inherit system
packages because PyGObject is supplied by the OS:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
(cd sidecar && npm install)
```

Typical commands:

```bash
.venv/bin/python -m bgtracker
.venv/bin/python -m bgtracker --overlay
.venv/bin/python -m bgtracker --overlay --replace
.venv/bin/python -m bgtracker --overlay --demo
.venv/bin/python -m bgtracker doctor
.venv/bin/python -m bgtracker stats
.venv/bin/python -m bgtracker resim 40
.venv/bin/python -m bgtracker settings
.venv/bin/python -m bgtracker history
.venv/bin/python -m bgtracker --replay FILE --odds
```

Tests:

```bash
.venv/bin/python -m pytest tests/
.venv/bin/python -m pytest tests/test_mapper.py
.venv/bin/python -m pytest tests/test_mapper.py::test_only_equipped_trinkets_are_sent
.venv/bin/python -m pytest -k trinket
```

There is no configured linter or formatter. `pyflakes` may be available in the
venv as a quick extra check, but it does not replace tests. Node integration tests
are driven by pytest and skip themselves when Node is unavailable; `sidecar/package.json`
does not define a useful standalone test command.

## Architecture and ownership

The main path is intentionally one-directional:

```text
Power.log -> Tailer -> hslog exporter -> typed events -> Pipeline
                                                   |-> GTK overlay
                                                   |-> history.db
                                                   `-> Python mapper -> Node sidecar
```

- `bgtracker/discovery.py`: Wine/Proton discovery and Hearthstone log configuration.
- `bgtracker/logwatch/`: session selection and incremental, truncation-safe tailing.
- `bgtracker/parse/`: raw log packets to typed events. It owns hslog interaction.
- `bgtracker/state/`: hslog entity tree to plain frozen dataclasses and opponent
  memory. `BoardSnapshot` is the downstream contract.
- `bgtracker/app.py`: event fan-out, simulation scheduling, damage-cap handling,
  outcome classification, and history coordination.
- `bgtracker/sim/mapper.py`: `BoardSnapshot` to Firestone `BgsBattleInfo`.
- `bgtracker/sim/client.py`: serialized JSON-lines requests and sidecar lifecycle.
- `bgtracker/sim/cpu.py`: pre-exec CPU affinity and scheduling policy.
- `sidecar/`: worker lanes, trial sharding, watchdogs, and result pooling. The
  sidecar is a router around the pinned Firestone package, not a second simulator.
- `bgtracker/history/db.py`: schema, migrations, and every history write.
- `bgtracker/history/review.py`: GTK-free read queries and review models.
- `bgtracker/history/stats.py` and `resim.py`: calibration and regression analysis.
- `bgtracker/overlay/model.py`: retained UI state and diff rendering inputs.
- `bgtracker/overlay/window.py`, `hud.py`, `rail.py`, `widgets.py`: UI layout and
  widget plumbing.
- `bgtracker/overlay/theme.py`: all colors, dimensions, fonts, and CSS tokens.
- `bgtracker/settings.py`: canonical option metadata, validation, and live-apply
  channels. `config.example.toml` is generated from this schema.

Keep the overlay a listener, never a driver. To display new information, add it to
the state snapshot and typed event, then consume it in the overlay. Overlay code must
not reach back into hslog or parser entities.

## Load-bearing invariants

Read the nearby comments and relevant `CLAUDE.md` section before changing any of
these behaviors.

### Logs and game state

- Snapshot both boards on the authoritative `GameState` shop-to-combat transition,
  `BOARD_VISUAL_STATE` 1 -> 2. UI timing follows the separate `PowerTaskList`-derived
  `ShopReady` event; `CombatEnd` arrives while the player is still watching combat.
- Displayed turn is `(raw TURN + 1) // 2` because the tag advances for both shop and
  combat phases.
- Player identity is hero `PLAYER_ID` / `PlayerBoard.bg_player_id`, not `CONTROLLER`.
  Opponents can share a controller slot.
- An opponent outside `Zone.PLAY` is not dead. Latch elimination by stable player ID
  using observed health, and read placement across zones.
- A Kel'Thuzad ghost fight that damages the player is a loss. A no-damage result is
  ambiguous (`ghost`), not a confirmed tie or win; calibration excludes all flagged
  ghost fights.
- Placement tags can arrive after `STATE = COMPLETE`; defer `GameEnd` until placement
  appears or `finalize()` flushes it.
- Use a fresh hslog `LogParser` for every `CREATE_GAME` in a session log.
- Live minion `ATK` and `HEALTH` tags are authoritative. Never substitute printed card
  database stats for live stats.
- Tavern shop buffs are exact-ID player-owned enchantments whose values are in
  `TAG_SCRIPT_DATA_NUM_1/2`, not `ATK`/`HEALTH`. Do not prefix-match their sibling IDs.
- Equipped trinkets are identified by both type and `Zone.PLAY`; offers and countdown
  placeholders are not equipment. Pool spells use the stable card-DB flag because
  their runtime type morphs.
- The friendly hand is visible and mapped; the opponent hand is hidden and must remain
  empty. Hero powers are deliberately omitted from simulator input until their required
  per-power state can be modeled. Sending a bare ID produces incorrect odds.
- Initial catch-up rebuilds state and opponent memory but never simulates old combats.
  Preserve the tailer's startup high-water mark across every bounded slice; an absent or
  empty file has a zero mark, so its first later lines are live. Suppress intermediate
  shop forecasts and schedule at most one from the final reconstructed shop state.

### Simulator and process lifecycle

- The sidecar protocol is one JSON object per stdout line. Worker stdout and stderr
  stay captured; any inherited logging can corrupt the protocol.
- Pool raw counts and damage sums, then derive percentages once. Never average shard
  percentages. Damage ranges are necessarily trial-weighted approximations.
- Foreground combat and background shop forecasts use positional worker lanes. Replace
  a retired worker at its vacated index, and wait for readiness by lane, not whole pool.
- `SimClient._request` holds its lock through `readline()`. Reconfiguration must take
  that same lock before stopping the sidecar.
- Apply CPU policy in `preexec_fn` so every worker thread inherits it. The pre-exec
  applier must be thin and must never raise; degraded scheduling is preferable to a
  failed sidecar spawn.
- The watchdog may return completed shards and retire a hung worker. Preserve the
  deliberate-retirement guard so replacement does not cascade into unrelated failures.
- The simulator may get slower or return fewer trials under CPU isolation, but its
  mathematical result must not change because of scheduling policy. Record and assess
  `sims_run` as well as wall time.

### History and settings

- Existing SQLite databases do not gain columns from `CREATE TABLE IF NOT EXISTS`.
  Add combat columns through `_ADDED_COMBAT_COLUMNS`; use `_USER_VERSION` plus a
  guarded `_migrate()` block for one-time data changes.
- Timestamps are stored in UTC and grouped into local-calendar periods at read time.
- Replaying a session writes the same rows again. Preserve COALESCE upserts so a
  catch-up without predictions cannot erase stored odds. In `end_game`, first
  `ended_at` wins while a later non-null placement wins; the opposite COALESCE
  directions are intentional.
- MMR readings are manual snapshots. Do not interpolate unsupported per-game changes.
- `Config` fields define recognized keys; `cfg.extra` is for generated panel positions.
  One shared `Config` object is mutated in place so all live consumers see changes.
- Every setting needs both a `Config` field and a `Setting` entry with the correct
  live-apply channel. Update `bgtracker/settings.py`, not generated
  `config.example.toml` by hand.
- Config rewriting is intentionally line-based to preserve comments. Resetting a key
  removes it so the default can apply again.

### Overlay

- GTK and asyncio share one GLib event loop. Do not add application threads.
- Call `theme.register_fonts()` before constructing any widget. Pango caches fallback
  font resolution.
- CSS is generated with `string.Template`, not `str.format`. Keep design tokens in
  `theme.py`, and never add a bare `window { ... }` selector to a display-wide provider.
- Keep GTK-owned switch, scrollbar, spin-button, and titlebar geometry under the system
  theme; style geometry only on project-owned classes.
- Preserve click-through behavior. The settings gear is the only intentional clickable
  input rectangle; hover is detected by polling X11 rather than capturing the pointer.
- `HoverStrips` must be presented, its GLib poll stopped before destruction, and its
  geometry rebound whenever the overlay window is rebuilt.
- `render(window, state, previous)` intentionally diffs. Avoid redundant setters: not
  calling one can be behavior, such as banking a combat result until `ShopReady`.
- `gtk4-layer-shell` must load before libwayland; `bgtracker/__main__.py` handles the
  one-time `LD_PRELOAD` re-exec. Test changes through the normal entry point.

## Code and test conventions

- Follow the existing Python style: four spaces, type annotations on public contracts,
  dataclasses for plain state, `pathlib.Path` for paths, and focused helpers. Keep
  imports at module scope unless an optional GTK dependency or startup ordering requires
  otherwise.
- Preserve frozen snapshot/event dataclasses unless mutation is an explicit part of a
  state service. Make boundary conversions obvious and typed.
- Prefer comments that capture log evidence, ambiguity, or a non-obvious invariant.
  Avoid comments that merely narrate the next line.
- Keep GTK imports out of history read-side and other headless domain code.
- Keep tests deterministic and isolated from real XDG files. Use `tmp_path` and
  monkeypatch module path constants. Mock downloads in unit tests; do not make routine
  tests depend on a live network.
- Preserve exact CLI and protocol output where tests or external processes consume it.
- When a bug came from a real log sequence, reproduce the smallest relevant sequence
  in a synthetic fixture and assert at the highest useful boundary.

## Verification matrix

| Change area | Required verification |
|---|---|
| Anything | `.venv/bin/python -m pytest tests/` (the whole suite) |
| Parser, events, or state projection | Add synthetic log coverage; replay a relevant captured fixture when available |
| Mapper, sidecar, or pinned simulator version | Full suite plus `bgtracker resim`; judge Brier-score direction, `sims_run`, and wall time, not mean probability drift alone |
| Overlay widgets, theme, or panels | Full suite plus `--overlay --demo` on a display; inspect all demo states |
| Settings, `Config`, or option docs | Include `tests/test_settings_schema.py`; confirm the generated example remains in sync |
| Sidecar spawn, CPU policy, or worker lanes | Sim roundtrip/watchdog/lane tests plus a MangoHud capture analyzed by `scripts/frametime-report.py`; evaluate the 0.1% low, not only mean FPS |
| First overlay use on a machine | `.venv/bin/python scripts/layer-shell-probe.py` before `--overlay` |

`resim` is both the simulator regression harness and throughput benchmark. A package
upgrade is acceptable when predictions move toward actual outcomes (lower Brier score),
not merely because the mean drift is small. A board-specific watchdog error can be an
honest result; verify the pool heals and later requests still complete.

## Handoff

At completion, state what changed, which checks ran and their exact result, which checks
were skipped or unavailable, and any remaining risk. Do not describe a subset test run
as complete verification. If a manual display, live game, captured log, network, or
MangoHud check is required but unavailable, say so plainly.
