# Leaderboard tavern-tier design

## Problem

The overlay leaderboard shows each player's portrait, placement, and effective
health, but omits tavern tier. Tier is useful at a glance because it indicates
the quality of minions a player can buy and contributes to combat damage.

The combat snapshot already reads `PLAYER_TECH_LEVEL` from a hero, but the
lighter-weight `Standing` event does not carry it. Deriving the value from
opponent memory would be incorrect: a remembered board is deliberately stale,
while the current hero entity can continue reporting lobby state between
scouting encounters.

## Decision

Add a nullable `tier` field to `Standing` and project it directly from the
hero's `PLAYER_TECH_LEVEL` tag. A tag value from 1 through 6 is displayed;
missing or out-of-range data remains `None` and is shown as `T—`. The overlay
must not silently turn an absent tag into tier 1.

Treat `PLAYER_TECH_LEVEL` as a standings-changing hero tag so an upgrade emits
a fresh `Standings` event even when health, placement, and zone do not change.
The existing end-of-input-batch coalescing and standings equality check still
prevent redundant renders.

## Rail layout

Keep the rail at its existing 98 design-pixel width. Each row retains the hero
orb and overlapping placement badge. Replace the single right-side label with
a compact vertical status stack:

- effective health (`health + armor`) on the first line;
- `T1` through `T6`, or `T—` when unknown, on the second line.

The player's own row also shows numeric health. Its existing gold portrait
ring, placement badge, and text treatment already identify it, so replacing
health with `YOU` spends the only numeric slot on redundant information.

The tier line uses exact text as well as a distinct muted treatment so color is
not the only signal. Eliminated players keep a struck-through health value;
their last reported tier is dimmed but not struck through.

No setting is added. Tavern tier is core lobby information and the compact
layout does not add a panel or widen the overlay.

## Data path

```text
Power.log PLAYER_TECH_LEVEL
    -> BGExporter hero projection
    -> Standing.tier
    -> Standings event / OverlayState
    -> LeaderboardRail row
```

The overlay remains a listener. It does not query hslog entities or opponent
memory to obtain the tier.

## Verification

Regression coverage must prove:

- standings carry a known tier from the synthetic log;
- a standalone `PLAYER_TECH_LEVEL` change refreshes standings immediately;
- absent and invalid tag values remain unknown;
- the rail formats known and unknown tiers consistently;
- the player's row keeps numeric health rather than replacing it with `YOU`;
- demo standings exercise different tiers and the unknown state; and
- the full pytest suite passes.

The finished rail must also be inspected with `--overlay --demo` on a real
display at representative scales. If no display is available, report that
manual check as not run.
