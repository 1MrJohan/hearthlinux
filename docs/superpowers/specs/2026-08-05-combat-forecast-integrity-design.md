# Combat forecast turn and integrity hardening

## Problem and evidence

Two independent failures make a forecast look untrustworthy.

First, the medallion advances while the player is still watching the previous
fight.  Captured `Power.log` evidence from the 2026-08-05 session shows this
sequence on every round:

1. raw `TURN=2n` and `BOARD_VISUAL_STATE=2` start combat `n`;
2. `GameState` resolves the fight in under a second and emits
   `BOARD_VISUAL_STATE=1` followed by raw `TURN=2n+1`;
3. the `PowerTaskList` shop marker arrives 20--45 seconds later, when the
   animation the player sees actually finishes.

The parser correctly converts both raw values to displayed turns with
`(raw + 1) // 2`.  The defect is in retained overlay state: `TurnChange(n+1)`
overwrites the combat snapshot's `n` even though the overlay deliberately
continues to present that combat until `ShopReady`.

Second, several recorded forecasts are confidently wrong.  The clearest
recent example is combat row 2878: the pinned Firestone simulator predicts a
100% win against a board containing golden `BG36_211` (Cage Gnawer), but the
actual result is a loss.  The live card database describes Cage Gnawer's
combat trigger (every friendly Beast attack buffs the Beast board), while the
pinned `@firestone-hs/simulate-bgs-battle` 1.1.724 contains no implementation
for that card or any other inspected BG36 combat card.  Re-simulating the 40
newest rows at the same pin completes all 8,000 trials with only 0.04 points of
mean drift, proving that this is deterministic missing behavior rather than
Monte Carlo noise, timeout truncation, or worker pooling.

## Design

### Keep the visible turn on the visible phase

`OverlayApp` will retain a private `_current_turn` independently of the turn
currently rendered in `OverlayState`.

- Every `TurnChange` updates `_current_turn`.
- Outside combat it also updates `state.turn`, preserving the existing live
  recruit-phase behavior.
- While `_in_combat`, it does not overwrite `state.turn`; `CombatStart` and
  provisional `CombatForecast` continue to take the authoritative turn from
  their `BoardSnapshot`.
- `ShopReady` copies `_current_turn` to `state.turn` at the same moment that it
  changes the visible phase to recruit.  The medallion therefore says turn
  `n` for combat `n`, then turn `n+1` once the player can actually shop.
- Game boundaries reset both values so startup replay and a new hero select
  cannot inherit a stale turn.

This does not change parser turn arithmetic, history keys, opponent memory, or
simulator `currentTurn`; the captured logs confirm those are already correct.

### Advance the pin only after the replacement clears calibration

Version 1.1.730 was installed and tested, but on the newest 80 stored combats
it raised Brier from 0.076 to 0.100 over 51 decided fights and introduced
several large probability swings. That failed the repository's
direction-based acceptance rule, so both manifest and lock file were restored.

Firestone 1.1.732 was published on 2026-08-07 with implementations for the
current BG36 effects, including Tasty Lobster and Cage Gnawer. On a fixed copy
of the newest 80 history rows, 1.1.724 completed 46 comparable forecasts and
scored 0.129 Brier over 35 decided fights; 1.1.732 completed the same 46 and
scored 0.071. Both completed all 8,000 trials. Mean wall time rose from 1,170
ms to 1,268 ms, an acceptable cost for restoring the missing behavior. The
exact dependency therefore advances to 1.1.732 while the compatibility guard
continues to reject later combat effects that are absent from its catalog.

Refreshing Firestone's card data while retaining 1.1.724 also reproduced bad
swings before compatibility filtering (Brier 0.092 to 0.101 over 53 decided
fights). This proves that behavior code and metadata cannot be treated as
independently fresh inputs: new metadata does not teach old code new effects.

### Fail closed when the pinned package lacks a combat implementation

The mapper uses current HearthstoneJSON text to mark board, hand, trinket, and
attached-enchantment IDs whose visible action can change the present fight.
It requires both a combat trigger and an entity-changing action, excluding
recruit destinations such as the Tavern, Spellcraft, Activate, hand, or a
future turn. This prevents economy-only Deathrattles and Rally effects from
needlessly suppressing odds.

The sidecar builds its capability catalog from the exact installed package.
Firestone has two implementation paths, so the catalog combines `cardMappings`
with literal card IDs found in compiled `.js` legacy switch tables; checking
only `cardMappings` incorrectly rejected working older cards such as
`BG29_611`. Before dispatching trials it rejects either of these proven
mismatches:

- the mapper marked a current-combat effect but its ID is absent from the
  installed package's behavior code;
- behavior code exists, but the Firestone card database lacks the metadata it
  needs for tribes, premium variants, or summoned IDs.

The sidecar returns a structured `unsupported_cards` error instead of
simulating a vanilla stat block. Installing a newer package also invalidates a
card cache older than that package, while the worker guard still protects the
case where the upstream data feed itself lags. Visible combat-active hero
powers are rejected in Python because the mapper deliberately cannot supply
their required per-power `info` state.

Captured-log follow-up found another omission at the same boundary. Turn 7 of
game 143 recorded a 100% win over 8,000 trials and then lost because The Great
Akazamzarak's Pack Tactics (`TB_Bacon_Secrets_15`) was absent from the payload.
GameState had materialized the exact `Zone.SECRET` entity before the opponent
combat board became complete, and Firestone 1.1.724 already implemented it;
`project_player_board()` simply ignored every non-PLAY/non-HAND entity. Secrets
are now retained in `PlayerBoard`, serialized into history, sent as Firestone
`player.secrets`, and included in compatibility checks. An exposed secret with
no card ID blocks simulation. Restoring Pack Tactics to a temporary copy of the
recorded row changed the replay from 100% win to 0% win, matching the loss.

The overlay treats availability as retained state too. A combat with no final
prediction clears the previous fight and displays an unavailable message. A
failed or mapper-blocked shop forecast emits an empty `ShopForecast`, replacing
any odds calculated for the prior friendly board. `ShopBoard` clears that text
immediately during debounce and recalculation. Starting a new combat resets
`forecast_live`, and game boundaries clear retained next-opponent text, so a
window rebuilt after a scale or monitor change cannot resurrect stale odds.

## Tests and verification

- Overlay regression: combat turn 7 remains visible after `TurnChange(8)` and
  becomes 8 only on `ShopReady`; game reset clears the remembered turn.
- Sidecar unit/integration coverage: stat-only and recruit-only IDs remain
  simulatable; legacy switch implementations are recognized; Tasty Lobster
  and Cage Gnawer are present in the 1.1.732 catalog; an unknown future combat
  effect still returns `unsupported_cards` instead of a forecast.
- Full Python suite, including the real sidecar roundtrip/watchdog/lane tests.
- Full-history `bgtracker resim`, reporting Brier before/after, `sims_run`,
  wall time, and the deliberate loss of coverage for unsupported boards. The
  accepted 1.1.732 full-history run processed 1,551 non-ghost combats, retained
  1,414 comparable rows, and lowered Brier from 0.100 to 0.078 over 1,140
  decided fights. Trials were 3,602 minimum and 8,000 median/maximum, with one
  run below 4,000; wall time was 992 ms mean, 774 ms median, and 6,308 ms
  maximum.
- Overlay demo remains a manual display check and will be reported unavailable
  if no display is present.
