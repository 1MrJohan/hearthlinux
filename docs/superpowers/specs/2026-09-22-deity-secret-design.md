# Deity secret: forwarding the Old God to the simulator

## Problem

The 2026-09-15 patch added Aberrations and a lobby-wide **Old God deity** (C'Thun
`BGFYM_000`, Y'Shaarj `BGFYM_011`, and golden variants). The deity is not a minion on
the board at combat start. It lives as a hidden hero secret, `BG_OldGod` ("Secret Deity
[DNT]"), that counts friendly Aberration deaths and **awakens the deity mid-combat** after
three of them. Its stats grow across the game (Energizing Chamber, Aberration effects).

Simulator 1.1.755 implements this. It reads the deity from the secret:

| Sim field | Read as | Fallback |
|---|---|---|
| attack | `tags[BACON_OLD_GOD_ATTACK]` → `scriptDataNum2` | 1 |
| health | `tags[BACON_OLD_GOD_HEALTH]` → `scriptDataNum3` | 1 |
| which deity | `scriptDataNum6` (dbfId) → `tags[BACON_GLOBAL_OLD_GOD_DBID]` | **C'Thun** |
| countdown | `scriptDataNum1 \|\| 3`, decremented per death | — |

The mapper forwarded secrets with `scriptDataNum1/2` only. A deity secret sent that way
awakens as a `NUM_2`/1 C'Thun whatever the lobby's deity is. `3c18223` therefore blocked
the secret outright ("deity secret not modeled") until a captured game showed the log's
shape. The block has a cost the first real game made plain: **every player holds the
secret in an Aberration lobby**, so it suppressed odds for all ten fights of that game.

## Log evidence

Captured game: `Hearthstone_2026_09_22_14_11_15` (local fixture
`tests/fixtures/aberration-game.power.log`, not committed: 42 MB and carries other
players' battletags). Y'Shaarj lobby, `BACON_GLOBAL_OLD_GOD_DBID = 132532`.

The secret materializes in GameState like any combat secret:

```
SHOW_ENTITY - Updating Entity=[... cardId=BG_OldGod ...] CardID=BG_OldGod
    tag=CONTROLLER value=14
    tag=CARDTYPE value=SPELL
    tag=TAG_SCRIPT_DATA_NUM_1 value=3
    tag=TAG_SCRIPT_DATA_NUM_2 value=1
    tag=ZONE value=SECRET
    tag=QUEST_PROGRESS_TOTAL value=3
    tag=TAG_SCRIPT_DATA_NUM_3 value=1
    tag=TAG_SCRIPT_DATA_NUM_6 value=132532
    tag=BACON_DEITY_SIGIL value=1
```

Walking the game through the tracker's own parser and reading both sides at every
`CombatStart` (10 combats):

- **Both sides always have exactly one** `BG_OldGod` in `Zone.SECRET` at snapshot time.
  The friendly one is a single persistent entity; the opponent's is a fresh combat copy
  per fight (controller 14), as with other opponent secrets.
- **`NUM_1` is 3 at every snapshot.** Within a fight it counts 3 → 2 → 1 → 0, the deity
  is created (`FULL_ENTITY … BGFYM_011` in fight 3), and it resets to 3 afterwards.
- **`NUM_2`/`NUM_3` equal the player entity's `BACON_OLD_GOD_ATTACK`/`HEALTH` in all 20
  side-snapshots**, from 1/1 up to 248/254. The dedicated tags live on the *player*, never
  on the secret, so the sim's first-choice read finds nothing and falls through to the
  script data, which is where the value is.
- **`NUM_6` is the current deity's dbfId and can change mid-game.** The friendly deity
  went 132532 → 134634 (golden Y'Shaarj, "first 4 Aberrations" instead of 2) on turn 9.
  The lobby-wide `BACON_GLOBAL_OLD_GOD_DBID` would have kept reporting the plain one, so
  it is not a substitute.

## Decision

Read `TAG_SCRIPT_DATA_NUM_3` and `TAG_SCRIPT_DATA_NUM_6` onto `Secret` alongside
`num1/num2`, and forward them as `scriptDataNum3/6`. Lift the blanket block.

Alternatives rejected:

- **Send the player-level `BACON_OLD_GOD_*` tags as the secret's `tags`.** Equal today,
  but it is a second source for a value the secret already carries, and says nothing
  about which deity. One read, from the entity the sim is modelling.
- **Take the deity from `BACON_GLOBAL_OLD_GOD_DBID`.** Wrong after a golden deity, as
  above.

Two details:

- **Forward `scriptDataNum3/6` for the deity secret only.** Every other secret has always
  been sent without them, and at least one (`BG28_603`) does set `NUM_3/NUM_6` in real logs.
  Nothing shows what the sim would do with those values, and an explicit value changes how
  a secret's `??` fallbacks resolve, so every non-deity payload stays byte-identical.
  (Revised during implementation: the first draft said "only when nonzero", which the
  `BG28_603` logs would have broken.)
- **Keep failing closed on an incomplete deity secret.** Combat rows recorded before this
  change stored `Secret` without `num3/num6`, so they load with 0. A `BG_OldGod` with
  `num6 == 0` would silently simulate a 1-health C'Thun; `simulation_blocker` names it
  "deity secret incomplete" instead. Live snapshots always carry it (all 20 above did).

## Verification

- Synthetic test: a `BG_OldGod` in `Zone.SECRET` with `NUM_1/2/3/6` projects all four and
  maps to `scriptDataNum1/2/3/6`; a non-deity secret's payload is unchanged.
- Replay the captured game with `--odds`: every fight gets a forecast (0 of 10 before),
  compared with the recorded outcomes.
- `resim 800`: no stored row holds `BG_OldGod` except this game's ten, which now fail
  closed as incomplete, so the corpus must not move beyond noise.
