"""The settings window: a plain, dark, schema-driven preferences window.

Deliberately *not* a layer-shell surface. The overlay is click-through with no
keyboard input by design, and text entry and focus handling would fight all of
that; this is an ordinary window that happens to be painted Dark Oak.

Every row is generated from `bgtracker.settings.SETTINGS`, so a new option is a
schema entry and nothing here. Everything applies live — there is no Apply
button and no OK button, because there is nothing to confirm: `SettingsService`
validates, writes, and pushes each change at the running tracker as it is made.

Numeric edits are debounced. A `Gtk.SpinButton` emits `value-changed` per
keystroke and per click of the stepper, and one of these settings restarts a
Node process holding a 350MB card database per worker.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from bgtracker import diagnostics, doctor, logging_setup, settings as sett  # noqa: E402

from . import theme  # noqa: E402

log = logging.getLogger(__name__)

# Long enough to swallow a burst of stepper clicks or typing, short enough that
# the change still feels like it belongs to the interaction.
COMMIT_DELAY_MS = 400


def _title(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("settings-section")
    return label


def _button(label: str, *classes: str) -> Gtk.Button:
    """Every button we make. The class is what keeps our styling off GTK's
    own window controls, whose metrics must stay the theme's business."""
    button = Gtk.Button(label=label)
    button.add_css_class("settings-btn")
    for name in classes:
        button.add_css_class(name)
    return button


def _help(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("settings-help")
    label.set_wrap(True)
    label.set_max_width_chars(52)
    return label


class SettingsWindow(Gtk.ApplicationWindow):
    """One per process; opening it again raises the one already up."""

    _current: SettingsWindow | None = None

    @classmethod
    def open(cls, app, settings, on_reset_history=None) -> SettingsWindow:
        if cls._current is not None:
            cls._current.present()
            return cls._current
        window = cls(app, settings, on_reset_history)
        cls._current = window
        window.present()
        return window

    def __init__(self, application, settings: sett.SettingsService, on_reset_history=None):
        super().__init__(application=application)
        self.settings = settings
        self._on_reset_history = on_reset_history
        self._timers: dict[str, int] = {}
        self._refreshers: list = []
        self._loading = False
        self._status_tick: int | None = None
        self._status_values: dict[str, Gtk.Label] = {}

        self.set_title("Tracker Settings")
        self.set_default_size(880, 640)
        self.add_css_class("bg-settings")
        theme.register_fonts()
        self._css = theme.install_settings(self.get_display())

        header = Gtk.HeaderBar()
        header.set_show_title_buttons(True)
        # An explicit, non-ellipsizing title: GTK measures the default one
        # before the stylesheet's letter-spacing is applied, then trims it to
        # fit the width it measured.
        heading = Gtk.Label(label="Tracker Settings")
        heading.set_ellipsize(Pango.EllipsizeMode.NONE)
        header.set_title_widget(heading)
        history = _button("Match History")
        history.connect("clicked", self._open_history)
        header.pack_start(history)
        self.set_titlebar(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(self.stack)
        sidebar.set_size_request(190, -1)

        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        split.append(sidebar)
        split.append(self.stack)
        self.stack.set_hexpand(True)
        self.set_child(split)

        for section, title in sett.SECTIONS:
            page = self._schema_page(section)
            if section == "debug":
                self._add_debug_extras(page)
            self.stack.add_titled(self._scrolled(page), section, title)
        self.stack.add_titled(self._scrolled(self._reset_page()), "reset", "Reset")

        self.connect("close-request", self._on_close)
        self.connect("notify::visible", lambda *_: self._sync_status_tick())
        self.stack.connect("notify::visible-child-name", lambda *_: self._sync_status_tick())
        self._refresh()

    # -- page scaffolding ------------------------------------------------
    @staticmethod
    def _scrolled(child: Gtk.Widget) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(child)
        return scroller

    @staticmethod
    def _page() -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for setter in (box.set_margin_top, box.set_margin_bottom,
                       box.set_margin_start, box.set_margin_end):
            setter(20)
        return box

    def _schema_page(self, section: str) -> Gtk.Box:
        page = self._page()
        page.append(_title(dict(sett.SECTIONS)[section]))
        if section == "calibration":
            page.append(_help(
                "Normally set by dragging the boxes in layout mode. These are "
                "here so they can be nudged precisely, and reset."
            ))
        for setting in sett.by_section(section):
            page.append(self._row(setting))
        return page

    def _row(self, setting: sett.Setting) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.add_css_class("settings-row")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        name = Gtk.Label(label=setting.label, xalign=0)
        name.add_css_class("settings-label")
        text.append(name)
        text.append(_help(setting.help))
        row.append(text)

        control = self._control(setting)
        control.set_valign(Gtk.Align.CENTER)
        row.append(control)
        return row

    # -- controls --------------------------------------------------------
    def _control(self, setting: sett.Setting) -> Gtk.Widget:
        if setting.kind == "bool":
            return self._switch(setting)
        if setting.kind == "choice":
            return self._dropdown(setting, list(setting.choices))
        if setting.kind == "monitor":
            return self._dropdown(setting, self._monitors(), nullable=True)
        if setting.kind == "path":
            return self._path(setting)
        return self._number(setting)

    def _switch(self, setting: sett.Setting) -> Gtk.Widget:
        widget = Gtk.Switch()

        def on_toggle(_w, state):
            self._commit(setting.key, state)
            return False   # let GTK move the slider to its new position

        widget.connect("state-set", on_toggle)
        self._refreshers.append(
            lambda: widget.set_active(bool(self.settings.get(setting.key)))
        )
        return widget

    def _dropdown(self, setting: sett.Setting, values: list[str], nullable=False) -> Gtk.Widget:
        labels = ([setting.null_label] if nullable else []) + values

        def value_at(index: int):
            if nullable and index == 0:
                return None
            return labels[index]

        widget = Gtk.DropDown.new_from_strings(labels)
        widget.connect(
            "notify::selected",
            lambda w, _p: self._commit(setting.key, value_at(w.get_selected())),
        )

        def refresh():
            current = self.settings.get(setting.key)
            target = 0 if (nullable and current is None) else (
                labels.index(str(current)) if str(current) in labels else 0
            )
            widget.set_selected(target)

        self._refreshers.append(refresh)
        return widget

    def _number(self, setting: sett.Setting) -> Gtk.Widget:
        step = setting.step or 1
        frac = 0 if setting.kind == "int" else min(3, len(str(step).split(".")[-1]))
        spin = Gtk.SpinButton.new_with_range(
            setting.minimum if setting.minimum is not None else 0,
            setting.maximum if setting.maximum is not None else 10**6,
            step,
        )
        spin.set_digits(frac)
        spin.set_width_chars(8)
        spin.connect(
            "value-changed",
            lambda w: self._debounce(setting.key, lambda: w.get_value()),
        )
        if not setting.nullable:
            self._refreshers.append(
                lambda: self._quietly(spin.set_value, float(self.settings.get(setting.key)))
            )
            return self._with_unit(spin, setting)

        # Nullable numerics are "Auto or this value": the switch owns which,
        # and unsetting is what puts the derived default back.
        auto = Gtk.Switch()
        auto.set_tooltip_text(setting.null_label)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        caption = Gtk.Label(label=setting.null_label)
        caption.add_css_class("settings-help")
        box.append(caption)
        box.append(auto)
        box.append(spin)

        def on_auto(_w, state):
            spin.set_sensitive(not state)
            self._commit(setting.key, None if state else spin.get_value())
            return False

        auto.connect("state-set", on_auto)

        def refresh():
            current = self.settings.get(setting.key)
            self._quietly(auto.set_active, current is None)
            spin.set_sensitive(current is not None)
            if current is not None:
                self._quietly(spin.set_value, float(current))
            elif setting.minimum is not None:
                self._quietly(spin.set_value, float(setting.minimum))

        self._refreshers.append(refresh)
        return self._with_unit(box, setting)

    @staticmethod
    def _with_unit(control: Gtk.Widget, setting: sett.Setting) -> Gtk.Widget:
        """Tack the unit (`s`, `px`, `days`) after a numeric control, so `14`
        does not sit there ambiguous next to 'Prune logs older than'."""
        if not setting.unit:
            return control
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.append(control)
        unit = Gtk.Label(label=setting.unit.strip())
        unit.add_css_class("settings-help")
        row.append(unit)
        return row

    def _path(self, setting: sett.Setting) -> Gtk.Widget:
        entry = Gtk.Entry()
        entry.set_width_chars(30)
        entry.set_placeholder_text(setting.null_label)
        browse = _button("Browse…")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(entry)
        box.append(browse)

        def commit(_w=None):
            text = entry.get_text().strip()
            self._commit(setting.key, text or None)

        entry.connect("activate", commit)
        # Committing on focus-out as well, so a typed path is not lost by
        # clicking straight onto another row.
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _c: commit())
        entry.add_controller(focus)

        def on_browse(_b):
            chooser = Gtk.FileDialog()
            chooser.set_title("Hearthstone install folder")

            def done(dialog, result):
                try:
                    folder = dialog.select_folder_finish(result)
                except GLib.Error:
                    return  # cancelled
                if folder is not None:
                    entry.set_text(folder.get_path() or "")
                    commit()

            chooser.select_folder(self, None, done)

        browse.connect("clicked", on_browse)
        self._refreshers.append(
            lambda: self._quietly(
                entry.set_text, str(self.settings.get(setting.key) or "")
            )
        )
        return box

    @staticmethod
    def _monitors() -> list[str]:
        try:
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
            if display is None:
                return []
            monitors = display.get_monitors()
            return [
                monitors.get_item(i).get_connector() or f"monitor {i}"
                for i in range(monitors.get_n_items())
            ]
        except Exception:
            return []

    # -- committing ------------------------------------------------------
    def _quietly(self, setter, value) -> None:
        """Set a widget without its signal being read back as a user edit."""
        was, self._loading = self._loading, True
        try:
            setter(value)
        finally:
            self._loading = was

    def _debounce(self, key: str, getter) -> None:
        if self._loading:
            return
        existing = self._timers.pop(key, None)
        if existing is not None:
            GLib.source_remove(existing)

        def fire():
            self._timers.pop(key, None)
            self._commit(key, getter())
            return GLib.SOURCE_REMOVE

        self._timers[key] = GLib.timeout_add(COMMIT_DELAY_MS, fire)

    def _commit(self, key: str, value) -> None:
        if self._loading:
            return
        try:
            self.settings.set(key, value)
        except ValueError as exc:
            log.warning("rejected %s = %r (%s)", key, value, exc)
            self._refresh()

    def _refresh(self) -> None:
        self._loading = True
        try:
            for refresher in self._refreshers:
                refresher()
        finally:
            self._loading = False

    # -- debug page ------------------------------------------------------
    def _add_debug_extras(self, page: Gtk.Box) -> None:
        page.append(Gtk.Separator())
        page.append(_title("Status"))
        self._status_box = Gtk.Grid(row_spacing=4, column_spacing=18)
        page.append(self._status_box)
        self._render_status()

        page.append(Gtk.Separator())
        page.append(_title("Diagnosis"))
        page.append(_help(
            f"The log is written to {logging_setup.LOG_FILE}. A diagnostics "
            "bundle collects it together with your config, the checks below, "
            "and version information."
        ))

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        run = _button("Run diagnosis")
        run.connect("clicked", lambda _b: self._run_doctor())
        bundle = _button("Write diagnostics bundle")
        bundle.connect("clicked", lambda _b: self._write_bundle())
        buttons.append(run)
        buttons.append(bundle)
        page.append(buttons)

        self._output = Gtk.Label(label="", xalign=0)
        self._output.add_css_class("settings-mono")
        self._output.set_wrap(True)
        self._output.set_selectable(True)
        page.append(self._output)

    def _render_status(self) -> bool:
        """Refresh the status values. Runs once a second while the page shows.

        The rows are a fixed set in a fixed order, so the labels are built once
        and only the value text (and its ok/bad colour) changes on each tick —
        rebuilding fourteen widgets a second for two strings that moved would
        be pure churn.
        """
        rows = diagnostics.status.rows()
        if not self._status_values:
            grid = self._status_box
            for row, (label, _value, _state) in enumerate(rows):
                name = Gtk.Label(label=label, xalign=0)
                name.add_css_class("settings-help")
                shown = Gtk.Label(label="", xalign=0)
                shown.add_css_class("settings-label")
                grid.attach(name, 0, row, 1, 1)
                grid.attach(shown, 1, row, 1, 1)
                self._status_values[label] = shown
        for label, value, state in rows:
            shown = self._status_values[label]
            shown.set_label(value)
            for cls in ("settings-ok", "settings-bad"):
                shown.remove_css_class(cls)
            if state in ("ok", "bad"):
                shown.add_css_class(f"settings-{state}")
        return GLib.SOURCE_CONTINUE

    def _sync_status_tick(self) -> None:
        """Only poll status while the Debug page is actually on screen."""
        wanted = self.get_visible() and self.stack.get_visible_child_name() == "debug"
        if wanted and self._status_tick is None:
            self._status_tick = GLib.timeout_add_seconds(1, self._render_status)
        elif not wanted and self._status_tick is not None:
            GLib.source_remove(self._status_tick)
            self._status_tick = None

    def _run_doctor(self) -> None:
        self._output.set_label("running…")

        async def go():
            try:
                records = await doctor.collect(self.settings.cfg)
                self._output.set_label(doctor.format_records(records))
            except Exception as exc:
                self._output.set_label(f"diagnosis failed: {exc!r}")

        if sett.SettingsService.spawn(go()) is None:
            self._output.set_label(
                "diagnosis needs the tracker's event loop — run it from the overlay"
            )

    def _write_bundle(self) -> None:
        self._output.set_label("collecting…")

        async def go():
            try:
                path = await diagnostics.bundle(self.settings.cfg)
                self._output.set_label(f"written to {path}")
            except Exception as exc:
                self._output.set_label(f"bundle failed: {exc!r}")

        if sett.SettingsService.spawn(go()) is None:
            self._output.set_label(
                "bundle needs the tracker's event loop — run it from the overlay"
            )

    # -- reset page ------------------------------------------------------
    def _reset_page(self) -> Gtk.Box:
        page = self._page()
        page.append(_title("Reset"))
        self._reset_note = Gtk.Label(label="", xalign=0)
        self._reset_note.add_css_class("settings-note")
        self._reset_note.set_wrap(True)

        page.append(self._reset_action(
            "Panel layout",
            "Forget every dragged panel position, so the overlay works them out "
            "from your monitor again.",
            "Reset layout", self._reset_layout,
        ))
        page.append(self._reset_action(
            "Hover calibration",
            "Forget the leaderboard hover-box geometry and start from the "
            "defaults.",
            "Reset calibration",
            lambda: self._did(self.settings.reset_section("calibration"),
                              "Hover calibration reset."),
        ))
        page.append(self._reset_action(
            "All settings",
            "Every option on every page back to its default, including panel "
            "positions. Your match history is not touched.",
            "Reset everything", self._reset_all,
        ))
        page.append(self._reset_action(
            "Match history",
            "Deletes every recorded game and combat. This is also the "
            "sim-calibration corpus and the stored boards that let a "
            "mispredicted fight be re-simulated, so a backup is offered.",
            "Delete history…", self._confirm_history, danger=True,
        ))
        page.append(self._reset_note)
        return page

    @staticmethod
    def _reset_action(label, description, button_label, handler, danger=False) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.add_css_class("settings-row")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        name = Gtk.Label(label=label, xalign=0)
        name.add_css_class("settings-label")
        text.append(name)
        text.append(_help(description))
        row.append(text)
        button = _button(button_label, "danger" if danger else "gold")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: handler())
        row.append(button)
        return row

    def _did(self, changed: bool, message: str) -> None:
        self._reset_note.set_label(message if changed else "Already at the defaults.")
        self._refresh()

    def _reset_layout(self) -> None:
        self._did(self.settings.reset_panel_positions(), "Panel layout reset.")

    def _reset_all(self) -> None:
        self._did(self.settings.reset_all(), "Every setting is back to its default.")

    def _confirm_history(self) -> None:
        HistoryResetDialog(self, self.settings, self._on_reset_history).present()

    def _open_history(self, _button) -> None:
        # Imported here: the history window pulls in the review queries, which
        # nothing else in the settings path needs.
        from bgtracker.overlay.history_window import HistoryWindow

        HistoryWindow.open(self.get_application())

    # -- teardown --------------------------------------------------------
    def _on_close(self, *_):
        for handle in self._timers.values():
            GLib.source_remove(handle)
        self._timers.clear()
        if self._status_tick is not None:
            GLib.source_remove(self._status_tick)
            self._status_tick = None
        theme.uninstall(self._css, self.get_display())
        type(self)._current = None
        return False


class HistoryResetDialog(Gtk.Window):
    """Typed confirmation for the one destructive action in the whole app."""

    WORD = "DELETE"

    def __init__(self, parent: SettingsWindow, settings, on_reset_history):
        super().__init__(transient_for=parent, modal=True)
        self.parent_window = parent
        self.settings = settings
        self._on_reset_history = on_reset_history
        self.set_title("Delete match history")
        self.set_default_size(460, -1)
        self.add_css_class("bg-settings")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for setter in (box.set_margin_top, box.set_margin_bottom,
                       box.set_margin_start, box.set_margin_end):
            setter(20)
        box.append(_title("Delete match history"))
        box.append(_help(self._stakes()))

        self.backup = Gtk.CheckButton(label="Back the database up first")
        self.backup.set_active(True)
        box.append(self.backup)

        box.append(_help(f"Type {self.WORD} to confirm."))
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(self.WORD)
        box.append(self.entry)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_halign(Gtk.Align.END)
        cancel = _button("Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        self.confirm = _button("Delete history", "danger")
        self.confirm.set_sensitive(False)
        self.confirm.connect("clicked", lambda _b: self._delete())
        buttons.append(cancel)
        buttons.append(self.confirm)
        box.append(buttons)
        self.set_child(box)

        self.entry.connect(
            "changed",
            lambda w: self.confirm.set_sensitive(w.get_text().strip() == self.WORD),
        )

    def _stakes(self) -> str:
        try:
            from bgtracker.history.db import HistoryDB

            db = HistoryDB()
            games, combats = db.counts()
            db.close()
        except Exception:
            return "This cannot be undone."
        return (
            f"{games} games and {combats} combats will be deleted. Those combats "
            "are what the calibration table is scored against, and each one "
            "stores both boards so a bad prediction can be re-simulated offline. "
            "This cannot be undone."
        )

    def _delete(self) -> None:
        backup = self.backup.get_active()
        try:
            if self._on_reset_history is not None:
                # Goes through the live pipeline so its open connection is
                # reopened rather than left pointing at a deleted file.
                saved = self._on_reset_history(backup)
            else:
                from bgtracker.history.db import HistoryDB

                db = HistoryDB()
                saved = db.reset(backup)
                db.close()
        except Exception as exc:
            log.exception("history reset failed")
            self.parent_window._reset_note.set_label(f"Could not delete history: {exc}")
            self.close()
            return
        self.parent_window._reset_note.set_label(
            f"Match history deleted. Backup: {saved}" if saved
            else "Match history deleted."
        )
        self.close()
