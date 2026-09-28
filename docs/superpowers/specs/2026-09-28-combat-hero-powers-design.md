# Combat hero powers: sending the ones the log can describe

## Problem

The mapper never sends a hero power. A power whose text acts during combat instead
suppresses the forecast (`simulation_blocker` → "combat hero power not modeled"), because
omitting it simulates a different fight and a bare id with `info = 0` was seen swinging
a 16% combat to 0%. That was the right call while nothing was known about the per-power
state. It is also now the largest single source of missing odds.

Replaying `simulation_blocker` over every stored combat (2026-09-28):

| Power | Card id | Blocked combats | since 09-15 | friendly / opponent |
|---|---|---|---|---|
| Rapid Reanimation | `BG25_HERO_103p` | 139 | 19 | 68 / 71 |
| Swatting Insects | `TB_BaconShop_HP_086` | 76 | 24 | 29 / 47 |
| Wax Warband | `TB_BaconShop_HP_037a` | 75 | 21 | 50 / 25 |
| Embrace Your Rage | `TB_BaconShop_HP_103` | 43 | 17 | 15 / 28 |
| Wingmen | `TB_BaconShop_HP_069` | 40 | 12 | 3 / 37 |
| Fragrant Phylactery | `BG20_HERO_282p` | 12 | 4 | 0 / 12 |
| Broodmother | `BG22_HERO_305p` | 10 | 0 | 0 / 10 |
| Fire / Water / Lightning Invocation | `BG22_HERO_001p_t2..t4` | 12 | 1 | 0 / 12 |
| ALL Will Burn! | `TB_BaconShop_HP_061` | 6 | 1 | 0 / 6 |

404 of 4,444 stored combats; 97 of the 664 since 2026-09-15 (15%).

## What the simulator reads

Simulator 1.1.755 takes `player.heroPowers: BgsHeroPower[]`
(`{cardId, entityId, used, info, info2 … info6}`) and passes it through
`input-sanitation.js` unchanged. Per power, from `dist/cards/impl/hero-power/` and
`dist/simulation/start-of-combat/`:

| Power | Reads | Gate |
|---|---|---|
| Swatting Insects | nothing | none — left-most minion gets Windfury, Divine Shield, Taunt |
| Wingmen | nothing | none (`soc-illidan-hero-power.js`) |
| ALL Will Burn! | nothing | none |
| Fragrant Phylactery | nothing | none |
| Rapid Reanimation | the `BG25_HERO_103pe` enchantment on a minion | `used` **and** the enchantment |
| Wax Warband | `info3` (buff size; else the card DB's `NUM_3`, i.e. +1) | `used` |
| Embrace Your Rage | `info` = the summoned minion's **card id** | `used` |
| Broodmother | `info`, `avengeCurrent`, `avengeDefault` | avenge counter |
| Invocations | nothing | `used` |

Firestone's own log converter (`hs-game-converter-csharp-port`,
`BattlegroundsPlayerBoardParser.cs`) fills these from the hero-power entities in `PLAY`:
`Used = BACON_HERO_POWER_ACTIVATED == 1`, `Info … Info6 = TAG_SCRIPT_DATA_NUM_1 … 6`.
Embrace Your Rage is patched afterwards: `Info` becomes the card id of the newest minion
whose `CREATOR` is the power's entity.

## Log evidence

Session logs `Hearthstone_2026_09_23_17_15_53`, `…_09_24_13_37_05`, `…_09_24_16_32_13`,
plus the stored boards in `history.db`.

**The combat carries its own copy of the power.** At the `BOARD_VISUAL_STATE` 1→2
transition, alongside the `BACON_COMBAT_PHASE_HERO` copy, GameState creates a fresh
hero-power entity under the combat controller. Wax Warband, 13:39:32:

```
FULL_ENTITY - Creating ID=612 CardID=TB_BaconShop_HP_037a
    tag=CONTROLLER value=7
    tag=CARDTYPE value=HERO_POWER
    tag=TAG_SCRIPT_DATA_NUM_1 value=7
    tag=BACON_HERO_POWER_ACTIVATED value=1
    tag=START_OF_COMBAT value=1
    tag=TAG_SCRIPT_DATA_NUM_3 value=1
```

The recruit-phase entity (194) never carries `BACON_HERO_POWER_ACTIVATED`; its `NUM_3`
climbs +1 each time `NUM_1` (gold left to spend) wraps past 10 — the "(Improves after
you spend 10 Gold!)" text. The combat copy carries the current value. This is also why
every stored Wax Warband board reads `hero_power_used = False`: that field is
`EXHAUSTED`, a different tag, and meaningless for a passive power.

**The snapshot precedes the effect.** Of 75 stored Swatting Insects boards, none has
Windfury, Divine Shield and Taunt together on the left-most minion. The power has not
fired when the tracker snapshots, so sending it does not double-apply.

**Rapid Reanimation's state is already recorded.** Across all 139 stored boards,
`hero_power_used` (`EXHAUSTED`) and "exactly one minion carries `BG25_HERO_103pe`"
agree every time (120 used with the mark, 19 unused without). The enchantment is already
forwarded; the simulator only needs `used`. The stored `EXHAUSTED` is used for it rather
than `BACON_HERO_POWER_ACTIVATED`: no captured log contains a picked Rapid Reanimation,
so the combat copy's tags for it are unobserved, and the stored field is proven.

**Embrace Your Rage appears only as a hero-select offer** (`WAS_DISCOVER_OPTION`) in the
captured logs. With no log of it being played, which minion `CREATOR` points at, and
whether it is already on the board at snapshot time, is unknown.

## Design

An allow-list in `sim/mapper.py`, keyed by card id, where each entry builds the exact
`BgsHeroPower` the simulator reads for that power, or reports why it cannot. A power not
on the list behaves exactly as today: omitted, and blocking the forecast if its text acts
in combat.

| Power | Sent as | Missing state → |
|---|---|---|
| Swatting Insects, Wingmen, ALL Will Burn!, Fragrant Phylactery | `cardId`, `entityId`, zeros | — |
| Rapid Reanimation | `used = hero_power_used` | — |
| Wax Warband | `used = true`, `info3 = NUM_3` | block: "hero power state not recorded" |

**Wax Warband is always `used`, deliberately unlike Firestone.** Running the parser over
`…_09_24_13_37_05` shows the opponent's snapshot reads the combat copy
(`ACTIVATED = 1`, `NUM_3` 1 then 6, as above), but the friendly snapshot reads the
recruit-phase entity, the one that is in `PLAY` for your controller all game (the captured
aberration game reads entity 228 on every turn), and `ACTIVATED` has never been observed
set on it. Gating on it would silently drop your own buff. The power's text carries no
condition, and `NUM_3` is live on both entities, so `used` is sent as true.

`PlayerBoard` gains `hero_power_entity_id`, `hero_power_activated` (`bool | None`) and
`hero_power_nums` (`NUM_1 … NUM_6`, `tuple | None`). `None` means "recorded before this
field existed", not zero: Wax Warband sent as `info3 = 0` falls back to +1 while the real
buff was up to +6, so an old row must stay blocked rather than simulate at the floor —
the same rule as the deity secret's `NUM_6`. The loader tolerates their absence the way
it does `secrets`.

A sent power id also goes into `trackerCombatCardIds`, and `sidecar/compatibility.mjs`
checks `player.heroPowers` alongside minions, trinkets and secrets. A future package that
drops one of these implementations then fails closed with `unsupported_cards` instead of
silently simulating without it.

Deferred, each for a stated reason: **Embrace Your Rage** (no played sample),
**Broodmother** (avenge counter state, 10 combats, none recent), **Invocations** (the
power rotates each turn; unobserved). They stay blocked.

## Verification

- Unit: each allow-listed power's payload, the Wax Warband old-row block, and a
  non-listed combat power still blocking.
- **Accuracy** of the newly unblocked rows (Rapid Reanimation plus the four stateless
  powers: 273 stored combats, no stored prediction to drift from). Simulate each twice —
  with the power, and with it omitted — and compare Brier against the recorded outcomes.
  The power is only worth sending if including it lowers Brier.
- `resim 800`, judged by Brier direction as usual. 71 already-predicted rows carry an
  allow-listed power: they were simulated before the blocker existed, with the power
  omitted. Those are a natural A/B — the stored prediction is "without", the resim is
  "with" — and are where any movement should be. Every other row's payload is unchanged.
