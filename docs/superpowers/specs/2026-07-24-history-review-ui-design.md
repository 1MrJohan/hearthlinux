# Game history & MMR review UI — design

Date: 2026-07-24. Approved in conversation.

## Goal

A graphical way to review recorded matches and MMR over time: daily/weekly/monthly
win-loss and rating trends, a browsable game list, and a per-game combat drill-down.

## Decisions made

- **Form**: a standalone Dark Oak GTK4 window, `bgtracker history`, same pattern as
  the settings window (ordinary window, `theme.register_fonts()`, `prefer_dark()`,
  scoped CSS classes only — never a bare `window` selector).
- **MMR capture**: manual. Research confirmed no Hearthstone log file under Wine
  carries the player's own BG rating (Windows trackers read game memory; the public
  leaderboard API only covers 8000+ players). The window gets a rating entry field
  writing to the existing `ratings` table; the `bgtracker mmr` CLI stays.
- **Review depth**: game list plus combat drill-down (turn, opponent hero, predicted
  odds vs actual outcome, ghost flag). No board rendering in this iteration.
- **Stats**: an MMR trend chart (Cairo on `Gtk.DrawingArea`, theme tokens) with a
  range toggle, plus a per-period table (day/week/month) of games, average placement,
  top-4 rate, firsts, and net MMR.

## Architecture

New pure-query module **`history/review.py`** (no GTK imports), consumed by the new
window **`overlay/history_window.py`**. Read path opens the DB through `HistoryDB`
so migrations always apply. The window re-queries when it becomes active, so it is
current without any coupling to the live pipeline.

### `history/review.py`

- `mmr_series(db)` → `[(recorded_at, rating)]` ascending.
- `period_stats(db, bucket)` (`'day' | 'week' | 'month'`) → newest-first rows of
  `(period_label, games, avg_placement, top4, firsts, net_mmr)`. Games are grouped
  by **local** date of `started_at`. `net_mmr` is the difference between the last
  rating reading at/before the period's end and the last reading before its start;
  `None` when no readings bracket the period.
- `game_list(db)` → newest-first rows: id, started_at, hero, placement, final turn,
  and `mmr_delta` **only** when exactly one game sits between two consecutive
  readings (no invented numbers).
- `game_detail(db, game_id)` → the game's combats in turn order: turn, opponent
  hero, predicted win/tie/loss, outcome, ghost flag.

### `overlay/history_window.py`

Singleton-open `Gtk.ApplicationWindow` (same `open()` classmethod pattern as
`SettingsWindow`), class `bg-settings` so the existing settings stylesheet applies,
plus history-only classes in a new `theme.history_stylesheet()`.

Sections, vertically scrolled:
1. **MMR** — current rating, entry + "Record" button, trend line drawn on a
   `Gtk.DrawingArea` (range toggle 30d / 90d / all). Empty state: "No ratings
   recorded yet…".
2. **Periods** — Day/Week/Month toggle over a grid: period, games, avg place,
   top-4 %, 1sts, net MMR.
3. **Games** — one `Gtk.Expander` row per game (date, hero name, placement, turns,
   MMR Δ when attributable); expanding shows the combat timeline with predicted %
   against the actual result, ghosts marked.

### Wiring

- `history` subcommand in `__main__.py` (no layer-shell preload needed).
- A "Match history…" button in the settings window header bar.
- Refresh on `notify::is-active` (window regains focus → re-query).

## Testing

`tests/test_review.py` drives `history/review.py` against a temp-file `HistoryDB`
with hand-inserted rows: day/week/month bucketing, net-MMR bracketing, delta
attribution (unambiguous vs ambiguous), unfinished games, empty DB. UI stays thin;
`tests/test_overlay_theme.py`'s bare-`window`-selector guard must keep passing.
