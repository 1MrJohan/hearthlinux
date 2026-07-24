# Hide the enemy-board panel during combat

2026-07-24

## Goal

The "Enemy Board" overlay panel duplicates what the game itself is showing
while a fight plays out, and covers screen space during exactly the moments
the user is watching the board. Stop showing it. The scout popout (hovering
a leaderboard portrait to review a player's last-seen board) is unaffected.

## Design

- `OverlayApp._enter_combat` no longer sets `st.board`; the panel gets no
  content and `_set_content` keeps a content-less panel hidden.
- The `st.board = None` resets at `ShopReady` and `_clear_to_idle` remain as
  defensive clears; the ShopReady comment is updated since "the design shows
  the enemy board only during combat" no longer describes reality.
- The panel widget, its saved `pos_board_x/y`, and its edit-mode placeholder
  stay: layout mode still shows the frame, and bringing the panel back (or
  making it a setting) later is a one-line change.
- Demo mode drives the same `on_event` path, so `--overlay --demo` matches.

## Testing

The overlay-state test asserting the board appears on `CombatStart` flips to
asserting `st.board` stays `None` through combat.
