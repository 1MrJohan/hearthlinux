"""Settings window helpers that can be checked without opening the window."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from bgtracker.config import Config  # noqa: E402
from bgtracker.overlay import settings_window, theme  # noqa: E402


def test_switching_scale_off_auto_starts_from_the_size_on_screen():
    """Not the 0.5 floor: committing that rebuilt the overlay at a quarter size."""
    seed = settings_window._AUTO_SEEDS["overlay_scale"](Config())
    assert seed == theme.auto_scale(1440)
    assert seed > 1.5
