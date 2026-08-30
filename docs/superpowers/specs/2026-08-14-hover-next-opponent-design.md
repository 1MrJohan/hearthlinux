# Hover-only next-opponent design

## Problem

The overlay currently gives the upcoming opponent a permanent board panel while
also exposing every remembered opponent through the leaderboard scout popout.
Those two panels duplicate the same last-seen board and make the normal layout
busier than it needs to be.

## Decision

Remove the dedicated `next` panel from the overlay layout. Retain the upcoming
opponent's identity and shop forecast in overlay state, but render them only in
the existing scout popout while that opponent's leaderboard row is hovered.

The behavior is:

- With no opponent row hovered, no next-opponent board or shop forecast is
  visible.
- Hovering the upcoming opponent through either the in-game leaderboard strips
  or the overlay rail opens the scout popout. Its status identifies the player
  as the next opponent, shows the last-seen turn (or `not scouted yet`), and
  places any shop forecast beneath the remembered board.
- Hovering another player keeps the existing scout behavior and never shows the
  upcoming opponent's forecast on that player's board.
- If the friendly board, forecast, standings, or upcoming opponent changes while
  the pointer remains in place, the open scout popout refreshes in place.
- Leaving the opponent row clears the popout as before.

The hover surface stays click-through. This change does not add input regions,
pointer capture, polling, or threads.

## State and rendering

`OverlayState` retains `next_opponent_id` and `next_forecast`. The permanent
`next_board` view and its renderer/window setters are removed. `BoardView` gains
an optional forecast line so the diff renderer can repaint a hovered forecast
atomically with the board it belongs to.

`OverlayApp` derives the scout popout from the current hovered place,
standings, opponent memory, next-opponent identity, and forecast. That helper is
called both on pointer changes and on events that can change the derived view,
so a stationary pointer cannot leave stale odds or the wrong player visible.

## Verification

Regression tests cover:

- no next-opponent window updates before hover;
- the next opponent's board and odds appearing on hover;
- non-next opponents never inheriting those odds;
- live forecast invalidation and replacement while hover remains active;
- clearing the popout when hover ends; and
- full state restoration after an overlay rebuild.

The full pytest suite is required. The layout must also be reviewed through
`--overlay --demo` on a display; if a display is unavailable, report that check
as not run.
