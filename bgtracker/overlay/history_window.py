"""The match-history window: MMR trend, per-period results, game drill-down.

An ordinary window painted Dark Oak, exactly like the settings window (it even
carries the `bg-settings` class so that stylesheet's chrome applies). All data
comes from `history.review`; this file only renders it.

The database is opened per refresh and closed again. Reads are cheap at this
scale, a stale handle would survive a `Delete history` reset pointing at an
unlinked file, and the window re-queries whenever it becomes the active window
— so it is always current without any coupling to the live pipeline.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from bgtracker.data import cards  # noqa: E402
from bgtracker.history import review  # noqa: E402
from bgtracker.history.db import RATING_MAX, RATING_MIN, HistoryDB  # noqa: E402

from . import theme  # noqa: E402

log = logging.getLogger(__name__)

CHART_H = 260
RANGES = (("7 days", 7), ("14 days", 14), ("30 days", 30),
          ("90 days", 90), ("All", None))


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    value = hex_colour.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _nice_ticks(lo: float, hi: float, target: int = 4) -> list[float]:
    """Round gridline values spanning [lo, hi] — 6,200 / 6,400, not 6,266 / 6,719."""
    if hi == lo:
        lo, hi = lo - 50, hi + 50
    # At least 1: ratings are whole numbers, and a sub-1 step over a tight
    # spread (6143..6145) prints the same rounded label several times over.
    raw = max((hi - lo) / target, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(lo / step) * step
    count = math.ceil(hi / step) - math.floor(lo / step)
    return [start + k * step for k in range(count + 1)]


def _segments(points, breaks) -> list[list[int]]:
    """Indices of `points` split wherever a reading is a break.

    A line drawn straight across a break would claim a history nobody
    recorded — untracked games happened there — so the trend stops and
    restarts instead of interpolating the gap.
    """
    runs: list[list[int]] = []
    for i, (at, _rating) in enumerate(points):
        if not runs or at in breaks:
            runs.append([])
        runs[-1].append(i)
    return runs


def _title(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("settings-section")
    return label


def _cell(text: str, *classes: str, xalign: float = 0.0) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign)
    label.add_css_class("history-cell")
    for name in classes:
        label.add_css_class(name)
    return label


def _place_label(placement: int | None) -> Gtk.Label:
    if placement is None:
        label = Gtk.Label(label="—")
        label.add_css_class("settings-help")
    else:
        label = Gtk.Label(label=f"#{placement}")
        label.add_css_class(
            "history-place-first" if placement == 1
            else "history-place-top" if placement <= 4
            else "history-place-bot"
        )
    label.add_css_class("history-place")
    return label


def _delta_label(delta: int | None) -> Gtk.Label:
    if delta is None:
        label = Gtk.Label(label="")
    else:
        label = Gtk.Label(label=f"{delta:+d}")
        label.add_css_class("history-delta-up" if delta >= 0 else "history-delta-down")
    label.add_css_class("history-cell")
    return label


class HistoryWindow(Gtk.ApplicationWindow):
    """One per process; opening it again raises the one already up."""

    _current: HistoryWindow | None = None

    @classmethod
    def open(cls, app, focus_rating: bool = False) -> HistoryWindow:
        """Raise the window, building it if this is the first call.

        `focus_rating` puts the caret in the rating box: the overlay's
        end-of-game nudge exists to collect one number, and the layer-shell
        overlay cannot take the keystroke itself, so this window has to be
        ready to type into the moment it appears.
        """
        window = cls._current
        if window is None:
            window = cls._current = cls(app)
        window.present()
        if focus_rating:
            window._rating_entry.grab_focus()
        return window

    def __init__(self, application):
        super().__init__(application=application)
        self._series: list[tuple[datetime, int]] = []
        # Readings the chain of screen deltas does not join up to: untracked
        # games happened just before them (review.mmr_breaks).
        self._breaks: set[datetime] = set()
        self._range_days: int | None = 30
        self._bucket = "day"
        self._expanded: set[int] = set()
        self._hover: int | None = None
        # (left pad, x step, point count) of the last draw, for hover hit-testing
        self._chart_layout: tuple[float, float, int] | None = None

        self.set_title("Match History")
        self.set_default_size(920, 720)
        self.add_css_class("bg-settings")
        theme.register_fonts()
        self._css = theme.install_settings(self.get_display())
        self._css_extra = theme.install_history(self.get_display())

        header = Gtk.HeaderBar()
        header.set_show_title_buttons(True)
        heading = Gtk.Label(label="Match History")
        heading.set_ellipsize(Pango.EllipsizeMode.NONE)
        header.set_title_widget(heading)
        self.set_titlebar(header)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for setter in (page.set_margin_top, page.set_margin_bottom,
                       page.set_margin_start, page.set_margin_end):
            setter(20)
        page.append(self._mmr_section())
        page.append(Gtk.Separator())
        page.append(self._periods_section())
        page.append(Gtk.Separator())
        page.append(self._games_section())

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(page)
        self.set_child(scroller)

        self.connect("close-request", self._on_close)
        # Re-query when the window becomes active again: a game may have
        # finished (or a rating been recorded elsewhere) since it was last read.
        self.connect("notify::is-active", self._on_active)
        self._refresh()

    # -- sections ----------------------------------------------------------
    def _mmr_section(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(_title("MMR"))

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self._rating_now = Gtk.Label(label="—")
        self._rating_now.add_css_class("history-rating")
        top.append(self._rating_now)

        self._rating_note = Gtk.Label(label="", xalign=0)
        self._rating_note.add_css_class("settings-help")
        self._rating_note.set_hexpand(True)
        top.append(self._rating_note)

        self._rating_entry = Gtk.Entry()
        self._rating_entry.set_placeholder_text("current rating")
        self._rating_entry.set_width_chars(10)
        self._rating_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self._rating_entry.connect("activate", lambda _w: self._record_rating())
        record = Gtk.Button(label="Record")
        record.add_css_class("settings-btn")
        record.add_css_class("gold")
        record.connect("clicked", lambda _b: self._record_rating())
        top.append(self._rating_entry)
        top.append(record)
        box.append(top)

        self._chart = Gtk.DrawingArea()
        self._chart.set_content_height(CHART_H)
        self._chart.set_hexpand(True)
        self._chart.add_css_class("history-chart")
        self._chart.set_draw_func(self._draw_chart)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_chart_motion)
        motion.connect("leave", self._on_chart_leave)
        self._chart.add_controller(motion)
        box.append(self._chart)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        ranges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        ranges.add_css_class("linked")
        group = None
        for label, days in RANGES:
            button = Gtk.ToggleButton(label=label)
            button.add_css_class("settings-btn")
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.set_active(days == self._range_days)
            button.connect("toggled", self._on_range, days)
            ranges.append(button)
        controls.append(ranges)
        self._range_delta = Gtk.Label(label="", xalign=1)
        self._range_delta.set_hexpand(True)
        self._range_delta.add_css_class("history-cell")
        controls.append(self._range_delta)
        box.append(controls)
        return box

    def _periods_section(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        title = _title("Results")
        title.set_hexpand(True)
        head.append(title)
        dropdown = Gtk.DropDown.new_from_strings(["By day", "By week", "By month"])
        dropdown.connect("notify::selected", self._on_bucket)
        head.append(dropdown)
        box.append(head)

        self._period_grid = Gtk.Grid(row_spacing=8, column_spacing=28)
        self._period_grid.add_css_class("history-periods")
        box.append(self._period_grid)
        return box

    def _games_section(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(_title("Games"))
        self._games_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self._games_box)
        return box

    # -- data --------------------------------------------------------------
    def _refresh(self) -> None:
        try:
            db = HistoryDB()
            try:
                self._series = review.mmr_series(db)
                self._breaks = set(review.mmr_breaks(db))
                periods = review.period_stats(db, self._bucket)
                games = review.game_list(db)
                details = {g.id: review.game_detail(db, g.id) for g in games}
            finally:
                db.close()
        except Exception:
            log.exception("could not read history")
            self._rating_note.set_label("Could not read the history database.")
            return
        self._render_rating()
        self._render_range_delta()
        self._hover = None
        self._chart.queue_draw()
        self._render_periods(periods)
        self._render_games(games, details)

    def _render_rating(self) -> None:
        if self._series:
            at, rating = self._series[-1]
            self._rating_now.set_label(str(rating))
            self._rating_note.set_label(f"last recorded {at.strftime('%b %d, %H:%M')}")
        else:
            self._rating_now.set_label("—")
            self._rating_note.set_label(
                "No ratings recorded yet — enter your current MMR to start the trend."
            )

    def _record_rating(self) -> None:
        text = self._rating_entry.get_text().strip()
        try:
            rating = int(text)
            if not RATING_MIN <= rating <= RATING_MAX:
                raise ValueError
        except ValueError:
            self._rating_note.set_label(f"“{text}” is not a rating.")
            return
        try:
            db = HistoryDB()
            db.record_rating(rating)
            db.close()
        except Exception:
            log.exception("could not record rating")
            self._rating_note.set_label("Could not write to the history database.")
            return
        self._rating_entry.set_text("")
        self._refresh()

    # -- rendering ---------------------------------------------------------
    def _cutoff(self) -> datetime | None:
        if self._range_days is None:
            return None
        return datetime.now(timezone.utc).astimezone() - timedelta(days=self._range_days)

    def _visible_series(self) -> list[tuple[datetime, int]]:
        cutoff = self._cutoff()
        if cutoff is None:
            return self._series
        return [p for p in self._series if p[0] >= cutoff]

    def _draw_chart(self, _area, ctx, width, height) -> None:
        """One step per reading, not per unit of time.

        Ratings arrive in bursts — a dozen in an evening, then nothing for a
        week — so a time axis crushes each session into a blob and spends the
        width on the gaps between them. Spacing readings evenly gives every
        game the same room; day boundaries are drawn as faint rules instead.
        """
        points = self._visible_series()
        self._chart_layout = None
        pad_l, pad_r, pad_t, pad_b = 52, 18, 16, 26
        ctx.select_font_face("sans")
        ctx.set_font_size(12)
        if len(points) < 2:
            ctx.set_source_rgba(*_rgb(theme.DIM), 0.9)
            message = (
                "Record at least two ratings to draw a trend."
                if self._series else "No ratings recorded yet."
            )
            extents = ctx.text_extents(message)
            ctx.move_to((width - extents.width) / 2, height / 2)
            ctx.show_text(message)
            return

        values = [p[1] for p in points]
        ticks = _nice_ticks(min(values), max(values))
        lo, hi = ticks[0], ticks[-1]
        step = (width - pad_l - pad_r) / (len(points) - 1)
        bottom = height - pad_b
        self._chart_layout = (pad_l, step, len(points))

        def x(i: int) -> float:
            return pad_l + i * step

        def y(v: float) -> float:
            return pad_t + (hi - v) / (hi - lo) * (bottom - pad_t)

        # horizontal gridlines on round values
        ctx.set_line_width(1)
        for value in ticks:
            ctx.set_source_rgba(*_rgb(theme.GOLD), 0.12)
            ctx.move_to(pad_l, round(y(value)) + 0.5)
            ctx.line_to(width - pad_r, round(y(value)) + 0.5)
            ctx.stroke()
            label = f"{value:,.0f}"
            extents = ctx.text_extents(label)
            ctx.set_source_rgba(*_rgb(theme.DIM), 0.95)
            ctx.move_to(pad_l - 8 - extents.width, y(value) + 4)
            ctx.show_text(label)

        # a faint rule where each new day starts, labelled when there is room
        label_right = -1.0
        for i, (at, _v) in enumerate(points):
            if i and at.date() == points[i - 1][0].date():
                continue
            if i:
                rule_x = round(x(i) - step / 2) + 0.5
                ctx.set_source_rgba(*_rgb(theme.GOLD), 0.10)
                ctx.move_to(rule_x, pad_t)
                ctx.line_to(rule_x, bottom)
                ctx.stroke()
            label = at.strftime("%b %d")
            extents = ctx.text_extents(label)
            left = min(max(x(i) - extents.width / 2, pad_l - 12),
                       width - pad_r - extents.width)
            if left > label_right + 10:
                ctx.set_source_rgba(*_rgb(theme.DIM), 0.95)
                ctx.move_to(left, height - 8)
                ctx.show_text(label)
                label_right = left + extents.width

        # soft fill under the trend, then the trend itself — one run per
        # stretch the readings vouch for, broken where untracked games sit
        fill = cairo.LinearGradient(0, pad_t, 0, bottom)
        fill.add_color_stop_rgba(0, *_rgb(theme.GOLD), 0.28)
        fill.add_color_stop_rgba(1, *_rgb(theme.GOLD), 0.0)
        for run in _segments(points, self._breaks):
            ctx.new_path()
            ctx.move_to(x(run[0]), y(values[run[0]]))
            for i in run[1:]:
                ctx.line_to(x(i), y(values[i]))
            path = ctx.copy_path()
            ctx.line_to(x(run[-1]), bottom)
            ctx.line_to(x(run[0]), bottom)
            ctx.close_path()
            ctx.set_source(fill)
            ctx.fill()

            ctx.new_path()
            ctx.append_path(path)
            ctx.set_source_rgb(*_rgb(theme.GOLD))
            ctx.set_line_width(2.5)
            ctx.set_line_join(cairo.LINE_JOIN_ROUND)
            ctx.stroke()
            if len(run) == 1:
                # A lone reading between two breaks draws no line at all.
                ctx.arc(x(run[0]), y(values[run[0]]), 3, 0, 2 * math.pi)
                ctx.fill()
        ctx.new_path()

        # dots only while they stay distinct; the latest reading always gets one
        if step >= 9:
            for i, v in enumerate(values):
                ctx.arc(x(i), y(v), 2.5, 0, 2 * math.pi)
                ctx.fill()
        last = len(values) - 1
        ctx.arc(x(last), y(values[last]), 4.5, 0, 2 * math.pi)
        ctx.fill()

        hover = self._hover
        if hover is not None and hover < len(points):
            self._draw_hover(ctx, points, hover, x(hover), y(values[hover]),
                             width, pad_t, bottom)

    def _draw_hover(self, ctx, points, i, px, py, width, top, bottom) -> None:
        ctx.set_source_rgba(*_rgb(theme.INK), 0.35)
        ctx.set_line_width(1)
        ctx.move_to(round(px) + 0.5, top)
        ctx.line_to(round(px) + 0.5, bottom)
        ctx.stroke()
        ctx.set_source_rgb(*_rgb(theme.INK))
        ctx.arc(px, py, 5, 0, 2 * math.pi)
        ctx.fill()

        at, rating = points[i]
        head = f"{rating:,}"
        sub = at.strftime("%b %d, %H:%M")
        # A difference of readings, not a game's change: several games can
        # sit between two readings, and across a break untracked ones do.
        broken = at in self._breaks
        delta = rating - points[i - 1][1] if i and not broken else None
        if broken:
            sub += " · untracked games before"
        elif delta is not None:
            sub += " · since last reading"
        ctx.set_font_size(14)
        head_w = ctx.text_extents(head).x_advance
        ctx.set_font_size(12)
        delta_text = f"  {delta:+d}" if delta is not None else ""
        delta_w = ctx.text_extents(delta_text).x_advance
        sub_w = ctx.text_extents(sub).x_advance
        box_w = max(head_w + delta_w, sub_w) + 20
        box_h = 44
        left = px + 12 if px + 12 + box_w <= width - 4 else px - 12 - box_w
        top_y = min(max(py - box_h / 2, 4), bottom - box_h)

        ctx.rectangle(left, top_y, box_w, box_h)
        ctx.set_source_rgba(0.07, 0.05, 0.02, 0.94)
        ctx.fill_preserve()
        ctx.set_source_rgba(*_rgb(theme.GOLD), 0.6)
        ctx.stroke()

        ctx.set_font_size(14)
        ctx.set_source_rgb(*_rgb(theme.GOLD))
        ctx.move_to(left + 10, top_y + 19)
        ctx.show_text(head)
        if delta is not None:
            ctx.set_font_size(12)
            ctx.set_source_rgb(*_rgb(theme.WIN if delta >= 0 else theme.LOSS))
            ctx.show_text(delta_text)
        ctx.set_font_size(12)
        ctx.set_source_rgb(*_rgb(theme.DIM))
        ctx.move_to(left + 10, top_y + 36)
        ctx.show_text(sub)

    def _render_range_delta(self) -> None:
        points = self._visible_series()
        label = self._range_delta
        for name in ("history-delta-up", "history-delta-down"):
            label.remove_css_class(name)
        cutoff = self._cutoff()
        # The Results table's rule (review.net_mmr): the baseline is the last
        # reading *before* the range, so the games that moved the first
        # reading inside it count. Diffing the visible points instead left
        # this label disagreeing with the Net MMR column on the same screen.
        delta = review.net_mmr(
            self._series,
            cutoff or datetime.min.replace(tzinfo=timezone.utc),
            datetime.max.replace(tzinfo=timezone.utc),
        )
        if delta is None or not points:
            label.set_label("")
            return
        span = next(name for name, days in RANGES if days == self._range_days)
        span = "all time" if self._range_days is None else f"last {span}"
        count = f"{len(points)} reading{'' if len(points) == 1 else 's'}"
        label.set_label(f"{delta:+,d} over {span} · {count}")
        label.add_css_class("history-delta-up" if delta >= 0 else "history-delta-down")

    def _render_periods(self, rows: list[review.PeriodRow]) -> None:
        grid = self._period_grid
        while (child := grid.get_first_child()) is not None:
            grid.remove(child)
        if not rows:
            note = Gtk.Label(label="No recorded games yet.", xalign=0)
            note.add_css_class("settings-help")
            grid.attach(note, 0, 0, 6, 1)
            return
        for col, name in enumerate(
            ("Period", "Games", "Avg place", "Top 4", "1sts", "Net MMR")
        ):
            head = Gtk.Label(label=name, xalign=0)
            head.add_css_class("history-head")
            grid.attach(head, col, 0, 1, 1)
        for r, row in enumerate(rows, start=1):
            # Over finished games: an unfinished one (abandoned at hero
            # select, tracker stopped) has no placement to be top 4 or not.
            top4 = (f"{row.top4} ({100 * row.top4 / row.placed:.0f}%)"
                    if row.placed else "—")
            avg = f"{row.avg_placement:.2f}" if row.avg_placement is not None else "—"
            grid.attach(_cell(row.period), 0, r, 1, 1)
            grid.attach(_cell(str(row.games)), 1, r, 1, 1)
            grid.attach(_cell(avg), 2, r, 1, 1)
            grid.attach(_cell(top4), 3, r, 1, 1)
            grid.attach(_cell(str(row.firsts)), 4, r, 1, 1)
            grid.attach(_delta_label(row.net_mmr), 5, r, 1, 1)

    def _render_games(
        self,
        games: list[review.GameRow],
        details: dict[int, list[review.CombatRow]],
    ) -> None:
        box = self._games_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        if not games:
            note = Gtk.Label(label="No recorded games yet.", xalign=0)
            note.add_css_class("settings-help")
            box.append(note)
            return
        for game in games:
            expander = Gtk.Expander()
            expander.set_label_widget(self._game_header(game))
            expander.set_child(self._combat_grid(details.get(game.id, ())))
            # The focus-in refresh rebuilds every row; without this it would
            # also slam shut whatever the user was reading.
            expander.set_expanded(game.id in self._expanded)
            expander.connect("notify::expanded", self._on_expanded, game.id)
            box.append(expander)

    def _on_expanded(self, expander: Gtk.Expander, _param, game_id: int) -> None:
        if expander.get_expanded():
            self._expanded.add(game_id)
        else:
            self._expanded.discard(game_id)

    def _game_header(self, game: review.GameRow) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.append(_place_label(game.placement))
        when = _cell(game.started_at.strftime("%a %b %d  %H:%M"))
        when.add_css_class("settings-help")
        row.append(when)
        hero = _cell(cards.name(game.hero_card_id))
        hero.set_hexpand(True)
        row.append(hero)
        if game.final_turn:
            turns = _cell(f"{game.final_turn} turns")
            turns.add_css_class("settings-help")
            row.append(turns)
        row.append(_delta_label(game.mmr_delta))
        return row

    @staticmethod
    def _combat_grid(combats) -> Gtk.Widget:
        grid = Gtk.Grid(row_spacing=2, column_spacing=18)
        grid.set_margin_start(30)
        grid.set_margin_top(4)
        grid.set_margin_bottom(8)
        if not combats:
            note = Gtk.Label(label="No combats recorded for this game.", xalign=0)
            note.add_css_class("settings-help")
            grid.attach(note, 0, 0, 4, 1)
            return grid
        for r, combat in enumerate(combats):
            grid.attach(_cell(f"T{combat.turn}"), 0, r, 1, 1)
            opponent = _cell(f"vs {cards.name(combat.opponent_hero)}")
            opponent.set_hexpand(True)
            grid.attach(opponent, 1, r, 1, 1)
            if combat.predicted_win is not None:
                predicted = _cell(
                    f"sim {combat.predicted_win:.0f}% win", xalign=1.0
                )
                predicted.add_css_class("settings-help")
            else:
                predicted = _cell("no odds", "settings-help", xalign=1.0)
            grid.attach(predicted, 2, r, 1, 1)
            if combat.outcome is None:
                result = _cell("?", "settings-help", xalign=1.0)
            else:
                shown = (
                    "ghost fight"
                    if combat.is_ghost and combat.outcome != "loss"
                    else combat.outcome
                )
                result = _cell(
                    shown.upper(), f"history-combat-{combat.outcome}", xalign=1.0
                )
            grid.attach(result, 3, r, 1, 1)
        return grid

    # -- plumbing ------------------------------------------------------------
    def _on_range(self, button: Gtk.ToggleButton, days: int | None) -> None:
        if button.get_active():
            self._range_days = days
            self._hover = None
            self._render_range_delta()
            self._chart.queue_draw()

    def _on_chart_motion(self, _ctrl, mx: float, _my: float) -> None:
        if self._chart_layout is None:
            return
        pad_l, step, count = self._chart_layout
        index = min(max(round((mx - pad_l) / step), 0), count - 1)
        if index != self._hover:
            self._hover = index
            self._chart.queue_draw()

    def _on_chart_leave(self, _ctrl) -> None:
        if self._hover is not None:
            self._hover = None
            self._chart.queue_draw()

    def _on_bucket(self, dropdown, _param) -> None:
        self._bucket = ("day", "week", "month")[dropdown.get_selected()]
        self._refresh()

    def _on_active(self, *_args) -> None:
        if self.get_property("is-active"):
            self._refresh()

    def _on_close(self, *_):
        theme.uninstall(self._css, self.get_display())
        theme.uninstall(self._css_extra, self.get_display())
        type(self)._current = None
        return False
