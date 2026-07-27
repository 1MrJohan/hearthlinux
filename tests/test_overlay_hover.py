"""The hover column's pointer poll must not outlive its window.

The poll is a GLib timeout source, and a GLib source is owned by the main loop,
not by the widget that started it. `OverlayApp._build_hover()` destroys and
rebuilds `HoverStrips` on every HOVER-channel change — which includes all five
`leaderboard_*` calibration settings, the ones a user nudges repeatedly — so a
poll that is never cancelled leaves one extra 12.5Hz X11 query per nudge, each
still driving the *shared* `on_slot` callback from its own stale `_current` and
a `rail_rect` bound to an already-destroyed `OverlayWindow`.

No display: the teardown is pure bookkeeping over one attribute, so the window
is built with `__new__` and its fields set directly, as in
`test_overlay_input_region.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
pytest.importorskip("gi.repository.Gtk4LayerShell")

from gi.repository import GLib  # noqa: E402

from bgtracker.overlay.hover import HoverStrips  # noqa: E402


def _stopped_strips(source=None):
    strips = HoverStrips.__new__(HoverStrips)
    strips._poll_source = source
    return strips


def test_stop_cancels_the_poll_source(monkeypatch):
    removed = []
    monkeypatch.setattr(GLib, "source_remove", removed.append)

    strips = _stopped_strips(source=1234)
    strips.stop()

    assert removed == [1234]
    assert strips._poll_source is None


def test_stop_is_idempotent(monkeypatch):
    """`_build_hover` calls it, and the destroy handler calls it again — a
    second `source_remove` on a dead id logs a GLib critical."""
    removed = []
    monkeypatch.setattr(GLib, "source_remove", removed.append)

    strips = _stopped_strips(source=99)
    strips.stop()
    strips.stop()

    assert removed == [99]


def test_stop_on_a_window_that_never_polled_is_a_no_op(monkeypatch):
    # Layout mode never sets up the poll at all, but is destroyed the same way.
    monkeypatch.setattr(GLib, "source_remove", lambda _id: pytest.fail("nothing to remove"))
    _stopped_strips(source=None).stop()


def test_a_stopped_poll_refuses_to_run_again():
    """Belt and braces: a callback already queued when `stop()` ran would
    otherwise still fire once, against a window on its way out."""
    strips = _stopped_strips(source=None)
    assert strips._poll_pointer() == GLib.SOURCE_REMOVE


def test_the_app_stops_the_poll_before_destroying_the_window():
    """The ordering is the fix. Destroying without stopping leaks the source,
    so `_build_hover` must call `stop()` on the outgoing window first."""
    from bgtracker.overlay.app import OverlayApp

    calls = []

    class FakeStrips:
        def stop(self):
            calls.append("stop")

        def destroy(self):
            calls.append("destroy")

    app = OverlayApp.__new__(OverlayApp)
    app.hover = FakeStrips()
    app.settings = type("S", (), {"cfg": type("C", (), {"hover_strips": False})()})()

    app._build_hover()

    assert calls == ["stop", "destroy"]
    assert app.hover is None


# -- HUD hover is about the HUD -----------------------------------------
def test_hud_hover_only_touches_the_hud():
    """`_on_hud_hover` must not present the hover window.

    That `present()` was the last line of the HoverStrips *construction* block
    (d5a62b0). a6d19a3 inserted this method directly above it, and the call —
    unchanged context in the diff — was silently absorbed into the new method.
    Construction then had no present, which is what left the window unrealized
    and made destroying it segfault; the symptom here is only the tell, which is
    that it fired on un-hover too, where raising a window means nothing.
    """
    from bgtracker.overlay.app import OverlayApp

    calls = []

    class FakeWindow:
        def set_hud_hovered(self, hovered):
            calls.append(("hud", hovered))

    class FakeStrips:
        def present(self):
            calls.append(("present", None))

    app = OverlayApp.__new__(OverlayApp)
    app.window = FakeWindow()
    app.hover = FakeStrips()

    app._on_hud_hover(True)
    app._on_hud_hover(False)

    assert calls == [("hud", True), ("hud", False)]


def test_hud_hover_before_the_window_exists_is_a_no_op():
    """The poll can fire between a rebuild's destroy and its replacement."""
    from bgtracker.overlay.app import OverlayApp

    app = OverlayApp.__new__(OverlayApp)
    app.window = None
    app.hover = None
    app._on_hud_hover(True)   # must not raise


# -- the window must be realized before it can be destroyed -------------
LAYER_SHELL_LIB = "/usr/lib/libgtk4-layer-shell.so"

needs_display = pytest.mark.skipif(
    not os.environ.get("WAYLAND_DISPLAY") or not Path(LAYER_SHELL_LIB).exists(),
    reason="needs a Wayland session with gtk4-layer-shell",
)


def _run_in_gtk(body: str) -> subprocess.CompletedProcess:
    """Run a snippet inside a real Gtk.Application, out of process.

    Out of process because the failure being guarded against is a **segfault**,
    which no in-process assertion can survive: the exit code is the assertion.
    """
    script = textwrap.dedent(f"""
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, GLib, Gtk

        from bgtracker.config import Config
        from bgtracker.overlay.hover import HoverStrips

        cfg = Config()
        app = Gtk.Application(application_id="dev.bgtracker.test",
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        state = {{}}

        def later():
            {body}
            app.quit()
            return GLib.SOURCE_REMOVE

        def activate(_a):
            # Constructed during `activate`, exactly as OverlayApp does it.
            state["w"] = HoverStrips(application=app, cfg=cfg, on_slot=lambda s: None)
            GLib.timeout_add(300, later)

        app.connect("activate", activate)
        app.run(None)
    """)
    env = {**os.environ, "LD_PRELOAD": LAYER_SHELL_LIB, "BGTRACKER_PRELOADED": "1"}
    return subprocess.run(
        [sys.executable, "-c", script], env=env, timeout=90,
        capture_output=True, text=True,
    )


@needs_display
def test_destroying_the_hover_window_does_not_crash():
    """The regression: `_build_hover` destroys and rebuilds on every HOVER-channel
    change, and GTK dereferences the window's surface while removing it from the
    application. An unrealized window has no surface, so this used to segfault
    (exit 139) the moment any hover setting was touched."""
    done = _run_in_gtk('state["w"].stop(); state["w"].destroy(); print("DESTROYED")')
    assert done.returncode == 0, f"crashed with {done.returncode}: {done.stderr[-2000:]}"
    assert "DESTROYED" in done.stdout


@needs_display
def test_the_hover_window_is_realized_once_built():
    """The root cause, stated directly. A window that is never presented is
    never realized, has no surface, and so also never runs its `realize`
    handler (the input region) and never draws the `hover_debug` boxes."""
    done = _run_in_gtk(
        'w = state["w"];'
        ' print("REALIZED", w.get_realized(), w.get_surface() is not None)'
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "REALIZED True True" in done.stdout, done.stdout
