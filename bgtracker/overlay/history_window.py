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
from datetime import datetime, timedelta, timezone

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from bgtracker.data import cards  # noqa: E402
from bgtracker.history import review  # noqa: E402
from bgtracker.history.db import HistoryDB  # noqa: E402

from . import theme  # noqa: E402

log = logging.getLogger(__name__)

CHART_H = 200
RANGES = (("7 days", 7), ("14 days", 14), ("30 days", 30),
          ("90 days", 90), ("All", None))
# Sanity bounds for a manual rating entry, not game rules: wide enough for any
# real ladder value, tight enough to catch a fat-fingered extra digit.
RATING_MIN, RATING_MAX = 0, 30_000


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    value = hex_colour.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


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
    def open(cls, app) -> HistoryWindow:
        if cls._current is not None:
            cls._current.present()
            return cls._current
        window = cls(app)
        cls._current = window
        window.present()
        return window

    def __init__(self, application):
        super().__init__(application=application)
        self._series: list[tuple[datetime, int]] = []
        self._range_days: int | None = 30
        self._bucket = "day"
        self._expanded: set[int] = set()

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
        box.append(self._chart)

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
        box.append(ranges)
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
    def _visible_series(self) -> list[tuple[datetime, int]]:
        if self._range_days is None:
            return self._series
        cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=self._range_days)
        return [p for p in self._series if p[0] >= cutoff]

    def _draw_chart(self, _area, ctx, width, height) -> None:
        points = self._visible_series()
        pad_x, pad_y = 56, 22
        ctx.select_font_face("sans")
        ctx.set_font_size(11)
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

        times = [p[0].timestamp() for p in points]
        values = [p[1] for p in points]
        t0, t1 = min(times), max(times)
        lo, hi = min(values), max(values)
        if hi == lo:
            lo, hi = lo - 50, hi + 50
        span_t = t1 - t0 or 1.0

        def x(t: float) -> float:
            return pad_x + (t - t0) / span_t * (width - pad_x - 14)

        def y(v: float) -> float:
            return pad_y + (hi - v) / (hi - lo) * (height - 2 * pad_y)

        # gridlines at min / mid / max, labelled
        ctx.set_line_width(1)
        for value in (lo, (lo + hi) / 2, hi):
            ctx.set_source_rgba(*_rgb("#d4af37"), 0.14)
            ctx.move_to(pad_x, y(value))
            ctx.line_to(width - 14, y(value))
            ctx.stroke()
            ctx.set_source_rgba(*_rgb(theme.DIM), 0.9)
            ctx.move_to(8, y(value) + 4)
            ctx.show_text(f"{value:.0f}")

        # date range along the bottom
        ctx.set_source_rgba(*_rgb(theme.DIM), 0.9)
        ctx.move_to(pad_x, height - 6)
        ctx.show_text(points[0][0].strftime("%b %d"))
        last = points[-1][0].strftime("%b %d")
        extents = ctx.text_extents(last)
        ctx.move_to(width - 14 - extents.width, height - 6)
        ctx.show_text(last)

        # the trend itself
        ctx.set_source_rgb(*_rgb(theme.GOLD))
        ctx.set_line_width(2)
        ctx.move_to(x(times[0]), y(values[0]))
        for t, v in zip(times[1:], values[1:]):
            ctx.line_to(x(t), y(v))
        ctx.stroke()
        for t, v in zip(times, values):
            ctx.arc(x(t), y(v), 3, 0, 6.2832)
            ctx.fill()

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
            top4 = f"{row.top4} ({100 * row.top4 / row.games:.0f}%)"
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
