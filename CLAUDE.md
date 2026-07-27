# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this software is

A Linux-native tracker and on-screen overlay for **Hearthstone Battlegrounds**, the
8-player auto-battler mode of Blizzard's Hearthstone. It shows live board state,
win/tie/loss odds for the upcoming combat, opponent-board memory, and local match
history.

Two properties shape almost every design decision:

- **Log-driven.** The only input is the game's own `Power.log`, tailed from disk.
  There is no game-memory reading, no injection, no Overwolf. Everything the tracker
  knows, it reconstructed from a text log — so "we can't see X" is usually a statement
  about the log, not about missing code.
- **Linux/Wine-native.** Hearthstone runs under Wine/Proton (Lutris, Bottles, Steam);
  the tracker auto-discovers the install across prefix layouts. The overlay is GTK4 +
  `gtk4-layer-shell` on Wayland, and the game itself is on XWayland — which is why
  pointer detection polls X11.

Card names/art come from HearthstoneJSON; combat odds come from Firestone's real
simulator, run as a Node sidecar. Card data and art are Blizzard's copyright; this is
a personal-use tool.

## Commands

```bash
# Setup (Arch/CachyOS system deps: python-gobject gtk4 gtk4-layer-shell python-xlib nodejs)
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
(cd sidecar && npm install)

# Run
.venv/bin/python -m bgtracker                       # live tracking, console output
.venv/bin/python -m bgtracker --overlay             # live tracking + on-screen overlay
.venv/bin/python -m bgtracker --overlay --replace   # …taking over from a running instance
.venv/bin/python -m bgtracker --overlay --demo      # overlay with fake data, no game needed
.venv/bin/python -m bgtracker stats                 # match history + sim calibration table
.venv/bin/python -m bgtracker resim 40              # re-simulate stored combats (regression harness)
.venv/bin/python -m bgtracker doctor                # diagnose the pipeline end to end
.venv/bin/python -m bgtracker settings              # settings window, standalone
.venv/bin/python -m bgtracker history               # match history & MMR review window
.venv/bin/python -m bgtracker mmr 8421              # record a rating reading by hand
.venv/bin/python -m bgtracker --replay FILE --odds  # replay a saved Power.log
.venv/bin/python -m bgtracker --no-names            # skip the card-name DB download

# Tests
.venv/bin/python -m pytest tests/
.venv/bin/python -m pytest tests/test_mapper.py   # one file
.venv/bin/python -m pytest tests/test_mapper.py::test_only_equipped_trinkets_are_sent
.venv/bin/python -m pytest -k trinket            # by name

# Capture a finished game's log as a fixture
scripts/capture-fixture.sh normal-game

# Overlay smoke test — run before the first --overlay on a new machine
.venv/bin/python scripts/layer-shell-probe.py
```

There is no linter configured; `pyflakes` is in the venv if you want a quick check.

Tests run against synthetic `Power.log` content built by `tests/synthetic.py`, plus any
real captured fixtures in `tests/fixtures/`. `test_sim_roundtrip.py` shells out to the
real Node sidecar and self-skips when `node` is missing.

## Architecture

```
Hearthstone (Wine/Proton) → Power.log → Tailer → LiveGameProcessor → typed events
                                                                        │
                              ┌─────────────┬───────────────────────────┤
                        GTK4 overlay   history.db (SQLite)      combat simulator
                    (gtk4-layer-shell)  + stats/calibration   (Node sidecar running
                                                              @firestone-hs/simulate-bgs-battle)
```

The spine is one-directional and worth internalizing before editing anything:

1. `logwatch/session.py` picks the newest `Logs/Hearthstone_*/` dir; `logwatch/tailer.py`
   reads new lines incrementally (survives the file not existing, truncation, partial writes).
2. `parse/exporter.py` feeds lines into `hslog`'s `LogParser` and subclasses its
   `EntityTreeExporter` to keep a **live** entity tree, emitting typed events as tags
   stream in. hslog is built for whole-game export; the subclass exports top-level
   packets as they complete instead.
3. `state/game.py` projects the hslog entity tree into plain frozen dataclasses
   (`Minion`, `PlayerBoard`, `BoardSnapshot`). **These snapshots are the contract** for
   everything downstream — overlay, sim mapper, and history all consume them and nothing
   else touches hslog types.
4. `app.py::Pipeline` fans events out: console (`headless.py`), opponent memory
   (`state/opponents.py`), the simulator, the history DB, and any registered `listeners`
   (the overlay subscribes here).
5. `sim/mapper.py` converts a `BoardSnapshot` into Firestone's `BgsBattleInfo`;
   `sim/client.py` talks JSON-lines over stdio to `sidecar/server.mjs`.

`sidecar/server.mjs` is a **router, not a simulator**: it shards trials across
`sim_workers` (default 4) `sim-worker.mjs` threads and pools the results. It never builds
a card DB itself, because each worker already pays ~350MB for one. Two rules there:

- **Pool counts and damage *sums*, then derive percentages once.** Averaging the shards'
  percentages would weight a shard that stopped early as heavily as one that finished.
  Damage *ranges* cannot be pooled exactly — the library clears its per-trial arrays
  before returning — so they are a trial-weighted mean of the shard bounds, and approximate.
- **Worker stdout is captured, never inherited.** The parent speaks JSON-lines on stdout;
  one stray `console.log` from inside the simulator would corrupt the protocol.

A run streams `{"id": n, "partial": {...}}` lines as it tightens before the final
`result`, so a number reaches the HUD in a fraction of the time a heavy 7v7 board takes.

The overlay is a *listener*, never a driver. `OverlayApp.on_event` is a pure
event→widget translation; adding data to the overlay means adding it to the snapshot
and the event, not reaching back into the parser.

`overlay/app.py` runs GTK and asyncio on **one** loop via `gi.events.GLibEventLoopPolicy`
(PyGObject ≥ 3.50) — tailer, sim client, and UI are all single-threaded. Don't add threads.

### Layer boundaries

| Concern | Lives in |
|---|---|
| Log discovery, `log.config` bootstrap | `discovery.py` |
| Raw line → typed event | `parse/` |
| Entity tree → dataclasses | `state/game.py` |
| Event fan-out, outcome classification | `app.py` |
| Snapshot → simulator JSON | `sim/mapper.py` |
| Widget layout & plumbing | `overlay/window.py`, `hud.py`, `rail.py`, `widgets.py` |
| Colours, sizes, fonts, stylesheet | `overlay/theme.py` (design tokens only) |
| What the overlay is showing, as data | `overlay/model.py` |
| Option metadata, validation, live apply | `settings.py` |
| Settings UI | `overlay/settings_window.py` |
| Log handlers and level | `logging_setup.py` |
| Live status and the diagnostics bundle | `diagnostics.py` |

## Domain: Hearthstone Battlegrounds

Background for reading this code. Terms in `code font` are the names actually used in
the codebase or the log.

**Match shape.** 8 players (`SLOTS = 8` in `overlay/rail.py`) each start with a hero and
that hero's starting health. A match alternates two phases:

- **Recruit phase / shop / tavern** (`SHOP = 1`) — you spend gold buying minions from
  Bob's tavern, sell, reposition, upgrade your tavern tier, and freeze the offers.
- **Combat** (`COMBAT = 2`) — you are paired against one other player and the two
  boards fight automatically. No input; the outcome is fully determined by board state
  at the moment combat starts. **This is exactly why odds can be simulated**: the
  tracker snapshots both boards at the shop→combat transition and re-runs the fight
  thousands of times.

The loser takes damage equal to the winner's surviving minions' tavern tiers plus the
winner's hero tier. Reaching 0 health eliminates you; **placement** is 1st (last alive)
through 8th. Top 4 is the usual success bar — see `history/stats.py`.

**Board.** Up to 7 minions in a left-to-right order that matters for combat
(`ZONE_POSITION`). Attackers alternate between the boards; the first attacker is the
side with more minions (random on a tie).

**Tavern tier** (1–6, `PLAYER_TECH_LEVEL` on the hero, `TECH_LEVEL` on a minion) gates
which minions the shop offers and how much damage you deal on a win. Upgrading costs
gold that decreases each turn you don't upgrade.

**Hero** — a `CardType.HERO` entity with a unique **hero power** (`CardType.HERO_POWER`)
that changes the whole game plan. **Bob** (`TB_BaconShopBob`, plus `_SKIN_*` variants) is
the tavern keeper and is *not* a player — the code filters him out of hero scans
everywhere. Placeholder heroes containing `"PH"` in the card id sit in play until the
hero pick resolves.

**Armor** is extra effective health granted by hero rating tier. Always read health as
`health + armor`; `Standing.total_health` and the sim's `hpLeft` both do.

**Minion keywords** the code models (`Minion.flags`, one letter each):

| Flag | Keyword | Effect in combat |
|---|---|---|
| `T` | Taunt | Must be attacked first |
| `D` | Divine Shield | Ignores the first instance of damage |
| `P` | Poisonous | Any damage it deals destroys the minion |
| `V` | Venomous | Like Poisonous, but consumed after one use |
| `W` | Windfury / Mega-Windfury | Attacks twice / four times |
| `R` | Reborn | Returns once with 1 health when it dies |
| `S` | Stealth | Can't be targeted by attacks until it attacks |
| `G` | Golden | The triple — 3 copies merge into one with doubled stats |

**Tribes / minion types** — Beast, Demon, Dragon, Elemental, Mech, Murloc, Naga, Pirate,
Quilboar, Undead. Only a rotating subset is in any given lobby (the sidecar accepts
`gameState.validTribes`, which the mapper does not yet send). Several tribes have
lobby-wide accumulating buffs stored as *player-level* tags, which the code surfaces as
the Buffs panel and forwards to the sim as `globalInfo`: Blood Gem (Quilboar), Elemental,
Pirate, and Tavern Spell. Undead has no player counter — it is tracked per-minion.

**Enchantments** (`CardType.ENCHANTMENT`, `ATTACHED` tag) are how every buff is actually
represented. A minion's printed stats are almost never its real ones, so **always read
live `ATK`/`HEALTH` tags, never the card DB's base stats**.

**Trinkets** (`CardType.BATTLEGROUND_TRINKET`) are equipped items offered at set turns.
Both players' equipped trinkets are visible, unlike hands. Distinguishing an *equipped*
trinket from an offer is a **zone** question, not a type one — equipped ones sit in
`Zone.PLAY`, offers and rejected discoveries pile up in `SETASIDE`/`REMOVEDFROMGAME`.
`BG30_Trinket_1st` / `BG30_Trinket_2nd` are not trinkets at all: they are countdown
placeholders announcing when the trinket shop opens.

**Tavern spells / pool spells** are persistent shop effects. The game *morphs their
runtime `CARDTYPE` between SPELL and TRINKET*, so they are classified by the stable
`isBattlegroundsPoolSpell` card-DB flag instead (`data/cards.py`). This is the one case
where trusting the entity type actively lies.

**Hand.** Several cards act from hand at the start of combat (Flighty Scout summons a
copy of itself, Diremuck Forager pulls Murlocs out, Choral Mrrrglr eats the hand's
stats), so the friendly hand is mapped to the simulator. **The opponent's hand is hidden
in the log** and always projects empty.

**Damage cap** (`BACON_COMBAT_DAMAGE_CAP`) limits combat damage in the early game. The
simulator reports uncapped numbers, so `app.py` clamps what is shown and recorded.

**Anomalies / quests** exist in some seasons and are not currently mapped.

## Log and tag facts that are easy to get wrong

These are hard-won; changing code near them without reading the surrounding comment
tends to reintroduce a fixed bug.

- **Phase detection** rides on `GameTag.BOARD_VISUAL_STATE` on the GAME entity
  (`1` = shop, `2` = combat). Both boards are fully materialized at the 1→2 transition,
  which is when to snapshot.
- **Two streams, two timings.** `Power.log` carries `GameState` (authoritative, what
  hslog parses) and `PowerTaskList` (the client's animation queue). GameState leaves
  combat ~1s after entering it — that is engine resolution time. The player is still
  watching the fight for a median 23s, up to 48s. **Snapshots and history follow
  `GameState`; anything on screen follows the `ShopReady` event**, which is raised off
  the `PowerTaskList` marker. Driving UI from `CombatEnd` makes the display change while
  the user is mid-battle.
- **Turn numbers.** BG increments the internal `TURN` tag for every recruit *and* combat
  phase. The turn shown in-game is `(raw + 1) // 2`.
- **`CONTROLLER` is not player identity.** Opponents share a controller slot (14).
  The stable per-player identity is the hero's `PLAYER_ID` tag — that is
  `PlayerBoard.bg_player_id`, and what opponent memory and the leaderboard rail key on.
  `PlayerBoard.player_id` is the *controller*: comparing it across a combat matches any
  opponent at all, so outcome classification reads a stranger's untouched HP as a tie.
- **Friendly-player detection**: only the local player's cards are revealed in `Zone.HAND`,
  so the first controller with a revealed hand card is us.
- **`Zone.PLAY` holds only the current pairing's heroes** — every other living
  opponent's hero rests in `SETASIDE`, so "not in PLAY" is *not* a death signal
  (that bug greyed out most of the leaderboard). Eliminated players reliably read
  `hp <= 0` (usually still in SETASIDE; occasionally GRAVEYARD with positive HP) —
  and the death must be **latched** per `PLAYER_ID`, because a Kel'Thuzad ghost
  fight reuses the dead player's hero entity and can hand it full HP back. Final
  placement must be read from any zone, not just PLAY.
- **Kel'Thuzad is not a player.** With an odd number of players left, somebody is paired
  against a *ghost* of an eliminated player (`TB_BaconShop_HERO_KelThuzad`). The ghost's
  hero is a dead player's entity and its HP reads **0 or negative**, so it cannot confirm
  a win: a win against a ghost is indistinguishable from a tie. All 13 in the author's
  history were therefore logged as ties, pulling the 90-100% calibration bucket down from
  87% to 75%. The ambiguous case gets its own `'ghost'` outcome (`state/game.py::is_ghost`).
  **A ghost fight can still cost you HP** — do not assume they are damage-free — and that
  is recorded as a genuine `'loss'`, which is why the loss check runs *before* the ghost
  check. Calibration excludes ghost fights via the `combats.opponent_is_ghost` flag rather
  than the outcome, because scoring only the legible ones would bias the table toward losses.
- **Placement tags land a few packets *after* the `STATE = COMPLETE`** tag, so `GameEnd`
  is deferred until one appears (or flushed by `finalize()`).
- **A fresh `LogParser` per `CREATE_GAME`.** A session log holds many games and hslog's
  player registry corrupts the entity tree when battletags reappear with new player ids.
- **Hero powers are deliberately NOT sent to the simulator.** The sim needs per-power
  `info` state; sending a bare id (info=0) makes it misapply even non-combat powers —
  verified swinging a 16% combat to 0%. `globalInfo` is safe and *is* sent.
- **Restarting mid-game is safe and cheap.** The tailer reads each session log from the
  top, so a fresh tracker replays the whole session (~3s for an 86MB log) and rebuilds
  current state. History rows are deduped by `log_id` (the game-start timestamp).
- Instances are deliberately **NON_UNIQUE** so a stale process can't swallow a new
  launch — which also means an old process keeps running the code it started with. Use
  `--replace` when testing changes.

## Odds accuracy workflow

The mapper covers stats, keywords, tier, enchantments, hand, and trinkets. Unmapped
features degrade *accuracy*, not correctness. `bgtracker stats` prints a calibration
table (predicted vs actual win rate per bucket) — that table is the evidence for what to
map next. Every combat row in `history.db` stores both board snapshots as JSON alongside
the prediction, so a mispredicted fight can be re-simulated offline from the row.

**`bgtracker resim [N]` is the regression harness.** It replays stored combats through
the current mapper and simulator and prints predicted-then vs predicted-now vs actual,
worst disagreement first. Run it after any change that could move the numbers; a mean
drift under ~0.2 points is Monte Carlo noise, anything larger is the change itself.

Two operational gotchas for `resim`, both learned the hard way:

- **A specific board can hang a sidecar worker indefinitely**, and `server.mjs` guards
  against it. The Firestone library enters a non-terminating trial and the worker goes
  CPU-bound — no message, error or exit — so the pool's crash recovery can't see it. The
  fix is a **per-job watchdog**: the client sends a `deadline` (just under its readline
  timeout), and if a job overruns it the sidecar returns whatever odds the finished shards
  gathered (or an error), then `terminate()`s the stuck worker(s) and respawns them
  (`retireWorker`; `entry.retired` keeps the deliberate kill from cascading through the
  exit handler). So a poison board costs only its *own* fight — the pool heals and the
  next request is fine. It is board-triggered, **not** a sequential-count ceiling (the
  same board hangs when simulated alone), so a long `resim` will still show the occasional
  single-combat error where a board genuinely hangs; that is the honest outcome, not a
  cascade.
- **The simulator's card *behavior* is frozen at the pinned
  `@firestone-hs/simulate-bgs-battle` version.** Card *data* (stats, tribes, keywords)
  auto-updates from the Firestone CDN (`sidecar/server.mjs`), but how a new minion resolves
  in combat is compiled into the package — so bump the pin when a new HS patch ships new
  mechanics, then `resim` and judge by whether predictions moved *toward* actual outcomes.
  Mean drift alone is not the test; direction is. (The 1.1.721→1.1.724 bump moved a mean
  ~2.5 points but *lowered* Brier 0.152→0.138 over 124 decided combats — an accuracy gain,
  so it stuck. A bump that raises Brier is a regression to reject, however small the drift.)

Read the calibration table with two things in mind:

- **Sampling error is not the problem.** Over 272 recorded combats the sim is
  well-calibrated in aggregate (mean predicted win 38.4% vs 37.9% actual). With 8000
  trials the 95% margin is about ±1 point, and `sims_run` is recorded per combat so a
  truncated run is visible rather than silently indistinguishable from a full one.
  Adding trials buys latency headroom, not accuracy.
- **Check the labels before believing the buckets.** The residual misses concentrate in
  the "certain" buckets, and data-quality bugs land there too — ghost fights and the
  `CONTROLLER`-vs-`PLAYER_ID` confusion between them accounted for more than half of the
  fights the sim called at 100% and got wrong.

## Overlay

The skin is **"Dark Oak"** — a carved-oak/gold-trim tavern look. The approved design
handoff is `design_handoff_overlay_redesign/README.md` and **its colours, radii, spacing
and typography are final**; treat them as the source of truth and put every token in
`overlay/theme.py`. `window.py` is layout and plumbing only.

Panels: HUD (phase title, turn medallion, win/tie/loss bar, damage pills), tavern
buffs, next opponent, scout popout, and the leaderboard rail of hero portraits. An
enemy-board panel exists but is dormant: it duplicated the fight the game itself was
showing, so nothing populates it — the scout popout is how a board gets reviewed.

The HUD forecast **stays open through the recruit phase** with the actual result under it
(`✔ WON · dealt 14`), rather than collapsing to a hover-only hint: a forecast only becomes
checkable once there is a result to check it against. `CombatResult` arrives with
`CombatEnd`, ~1s into a fight the player watches for 20-45s more, so the overlay banks it
and reveals it at `ShopReady` — revealing it on arrival spoils the battle being watched.

The **next-opponent panel** carries live recruit-phase odds against that player's
last-seen board, re-run (debounced) as you buy, sell and reposition. It is a guess and
says so: the opponent keeps shopping after you last saw them, so the board's age is
labelled next to the number.

Non-obvious constraints:

- **`theme.register_fonts()` must run before any widget construction.** Cinzel and
  Alegreya Sans (SIL OFL) ship in `bgtracker/assets/fonts/` and are registered at runtime
  with `Pango.FontMap.add_font_file`. Pango caches the face it resolves per description,
  so a single lookup beforehand permanently pins the overlay to the fallback font.
- **The stylesheet uses `string.Template` `$name` substitution, not `str.format`** — GTK
  CSS is full of literal braces.
- **Hovering never captures the pointer.** The window's input region is *almost* empty
  and the X11 cursor is polled instead, so Blizzard's own board-preview-on-hover keeps
  working and both show together. `overlay/hover.py` owns this. The one exception is
  the **settings gear** above the HUD's top-right corner: a single ~22px rect that
  swallows clicks instead of passing them to Hearthstone. `_input_rects()` is the whole
  list, and it should stay this short — every entry is a hole in the click-through
  guarantee.
- **`HoverStrips` must present itself, and must cancel its poll before it dies.**
  Both are load-bearing and neither is obvious, because the window is invisible in
  live mode and looks like it needs neither. It is rebuilt on every `HOVER` change —
  which is `hover_strips`, `hover_debug` **and all five `leaderboard_*` calibration
  settings**, the ones a user nudges repeatedly. Unpresented it is never realized, so
  it has no surface: the `realize` handler that uploads the input region never runs,
  the `hover_debug` boxes never draw, and `destroy()` **segfaults** — GTK dereferences
  the surface while removing the window from the application. Separately, the X11
  pointer poll is a GLib source and outlives the widget, so `stop()` before `destroy()`
  is what keeps ten calibration nudges from leaving eleven pollers fighting over the
  scout popout with stale geometry. `tests/test_overlay_hover.py` covers both, the
  crash out of process because an exit code is the only assertion a segfault can't eat.
- **Never write an unscoped `window { … }` rule.** These CSS providers attach to the
  *display*, not to a window, so a bare selector reaches every window the process
  opens. `theme.py` and `hover.py` each used to load their own
  `window { background: transparent }`, which is why the settings window would have
  come up transparent over a light system theme. Transparency is now
  `window.bg-overlay`, and `tests/test_overlay_theme.py` fails on any bare `window`
  selector.
- **Colours only on GTK's own widget parts.** Switch sliders, scrollbar sliders, spin
  steppers and the titlebar's window controls take their minimum sizes, padding and
  baselines from the system theme; overriding any of those makes GTK compute negative
  minimums and warn on every layout pass. Geometry belongs only to classes we create
  (`.settings-btn`, `.settings-row`). `theme.prefer_dark()` is what makes the parts we
  leave alone look right on a light system theme.
- **`gtk4-layer-shell` must link before libwayland**, which from Python means
  `LD_PRELOAD`; `__main__.py` re-execs itself once with it set.
- Panel sizes derive from monitor height by default so the HUD covers the same share of
  screen as the mock (1080p ~1.53, 1440p ~2.04, 4K capped at 3.0). Panels that outgrow
  their saved position get nudged back on-screen without rewriting the layout.
- Run Hearthstone in **borderless windowed** mode — exclusive fullscreen can cover the
  overlay.
- `--overlay --demo` renders every state (hero select, combat, shop, golden minion,
  eliminated player) through the real `on_event` wiring. Use it whenever changing the skin.

## Settings

`bgtracker/settings.py` is the source of truth for every option: one `Setting` per
key, carrying its type, bounds, label, help text and **channel**. That one table
drives the settings window's widgets, validation, reset-to-default, and the
generated `config.example.toml`. Adding an option means adding a `Config` field and
a `Setting` — nothing else, and `tests/test_settings_schema.py` fails if you do only
one of the two, or if the example file drifts.

`Config`'s *fields* are the recognised key list (`load_config` walks
`dataclasses.fields`). `cfg.extra` now holds only the machine-written
`pos_<panel>_x/y` positions, which are per-panel and so cannot be fields.

**Every setting applies live**, which is what the channel names: who has to be told.

| Channel | Applied by | Cost |
|---|---|---|
| `NONE` | nobody — read fresh from the shared `Config` (`poll_active`) | free |
| `SIM` | attribute assignment on the live `SimClient` | free |
| `SIM_RESPAWN` | `SimClient.reconfigure_workers`, **under the request lock** | seconds |
| `TAILER` | the tail loop re-resolves and rebuilds on its next pass | one poll |
| `OVERLAY` | `OverlayWindow.set_edit` in place | free |
| `OVERLAY_REBUILD` | window destroyed and rebuilt, then repainted from state | a frame |
| `HOVER` | the hover-strip window is rebuilt | a frame |
| `LOGGING` | handlers/level swapped on the root logger | free |

Three things make that safe, and all three are load-bearing:

- **One `Config`, mutated in place.** `SettingsService` owns it and everything
  downstream holds the same object. Handing out copies would make half the settings
  silently inert.
- **The sim lock.** `SimClient._request` holds `_lock` across its `readline()`
  await, so `reconfigure_workers` takes that lock before killing the sidecar.
  Without it a worker change during a combat raises `sidecar died mid-request` and
  that fight gets no odds.
- **The overlay state model** (`overlay/model.py`). See below.

`update_config_values()` is deliberately line-based rather than a TOML round-trip
(stdlib has no writer) so hand-written comments survive. `remove_config_keys()` is
its counterpart: resetting a setting means the key is *gone*, so the default
applies again — which update-or-append cannot express.

### The overlay state model

`OverlayApp` keeps an `OverlayState` and `render(window, state, previous)` is the
only thing that touches widgets. This exists because `overlay_scale` bakes into both
a display-wide CSS provider and every widget constructor, so changing it means
rebuilding the window — and before the state model a rebuilt window came up blank
until the next event, which mid-recruit-phase is half a minute.

**`render` diffs on purpose**, emitting only what changed. Partly for churn
(`_set_content` re-clamps the panel and re-uploads the input region), but mainly
because *not* calling a setter is behaviour: the combat result must stay banked
until `ShopReady`, not revealed the moment it lands mid-fight. A renderer that
pushed everything every time would quietly undo that.

A rebuild is `render(new_window, state)` with no `previous`. It must also re-bind
`HoverStrips` — that window holds `rail_rect`/`hud_rect` bound methods on a
*specific* `OverlayWindow`, and skipping the re-bind leaves the hover column
resolving slots against destroyed geometry.

### Debugging

- `log_to_file` (default on) keeps a rotating log at
  `~/.cache/hs-bg-tracker/bgtracker.log`. The tracker is normally launched *by the
  game*, so console output goes nowhere anybody reads. `-v` is a floor on the level,
  not a separate mechanism.
- `doctor.py` checks *yield* `(kind, text)` records; `run()` is the printing shell.
  Its CLI output is byte-for-byte what it always was — diff it after touching this.
- `diagnostics.status` is a mutable record the pipeline and tail loop poke as they
  work. Display-only: nothing reads it back to make a decision.
- `diagnostics.bundle()` writes config + doctor + versions + log tail to one file.

## Launching with the game

`scripts/steam-launch.sh` runs the tracker for exactly as long as Hearthstone is up —
point Steam's launch options at it, or run it standalone. It watches for the
`Hearthstone.exe` **process name** (never the command line, which also matches shells
that merely mention the game); override with `BGTRACKER_GAME_PROC`. Each start uses
`--replace`. Activity logs to `~/.cache/hs-bg-tracker/launch.log`.

A systemd user service is the tempting alternative and the wrong one: the overlay needs
`WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, `DISPLAY` and the layer-shell `LD_PRELOAD`, all of
which a child of the game launch inherits and a user unit does not.

First run writes `log.config` into the Hearthstone Wine prefix to enable Power/Bob
logging — **Hearthstone must be restarted once** after that. The file mirrors HDT's
generated one exactly (full key set, capitalized booleans, CRLF); the client's parser is
picky enough that a partial or LF-only file has been observed to break all logging.
