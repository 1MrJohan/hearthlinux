"""Grab pixels out of the Hearthstone window over X11.

The game runs on XWayland, so its window is an ordinary X window and
`get_image` returns its own contents: no Wayland screenshot portal, no
permission prompt, and nothing of the overlay, which is a layer-shell surface
the X server never sees. The same `python-xlib` the hover column polls the
pointer with.

Every failure — no display, the game closed, the window resized mid-grab, an
unexpected pixel format — comes back as `None` and drops the cached handles,
so the next call starts from scratch. Nothing here raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# WM_CLASS depends on the launcher (Steam gives `steam_app_<id>`, Lutris
# `hearthstone.exe`), so the title is the only stable handle.
WINDOW_NAME = "Hearthstone"


@dataclass(frozen=True)
class Pixels:
    """One grabbed rectangle, as three planes of 8-bit channel values."""

    width: int
    height: int
    r: bytes
    g: bytes
    b: bytes

    def ppm(self) -> bytes:
        """Binary PPM, which tesseract reads straight from stdin."""
        rgb = bytearray(self.width * self.height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = self.r, self.g, self.b
        return b"P6 %d %d 255\n" % (self.width, self.height) + bytes(rgb)


class GameWindow:
    """The Hearthstone X window, found by title and re-found when it goes away."""

    def __init__(self, name: str = WINDOW_NAME):
        self.name = name
        self._display = None
        self._window = None

    def size(self) -> tuple[int, int] | None:
        window = self._find()
        if window is None:
            return None
        try:
            geometry = window.get_geometry()
            return geometry.width, geometry.height
        except Exception as exc:
            self._drop(exc)
            return None

    def grab(self, x: int, y: int, width: int, height: int) -> Pixels | None:
        """The rectangle at window-relative (x, y), or None."""
        window = self._find()
        if window is None or width <= 0 or height <= 0:
            return None
        try:
            from Xlib import X

            image = window.get_image(x, y, width, height, X.ZPixmap, 0xFFFFFFFF)
        except Exception as exc:
            self._drop(exc)
            return None
        data = image.data if isinstance(image.data, bytes) else bytes(image.data)
        # 32 bits per pixel, BGRX, is what a 24-bit-depth XWayland window gives.
        # Anything else would be misread as colour, so refuse rather than guess.
        if len(data) != width * height * 4:
            log.debug("unexpected pixel format: %d bytes for %dx%d",
                      len(data), width, height)
            return None
        return Pixels(width, height, r=data[2::4], g=data[1::4], b=data[0::4])

    def close(self) -> None:
        if self._display is not None:
            try:
                self._display.close()
            except Exception:
                pass
        self._display = self._window = None

    def _find(self):
        if self._window is not None:
            return self._window
        try:
            if self._display is None:
                from Xlib import display as xdisplay

                self._display = xdisplay.Display()
            root = self._display.screen().root
            matches = list(self._named(root))
        except Exception as exc:
            self._drop(exc)
            return None
        if not matches:
            return None
        # Wine can leave small helper windows carrying the same title; the game
        # is the big one.
        self._window = max(matches, key=lambda pair: pair[1])[0]
        return self._window

    def _named(self, window):
        try:
            name = window.get_wm_name()
        except Exception:
            name = None
        if name == self.name:
            try:
                geometry = window.get_geometry()
                yield window, geometry.width * geometry.height
            except Exception:
                pass
        try:
            children = window.query_tree().children
        except Exception:
            return
        for child in children:
            yield from self._named(child)

    def _drop(self, exc: Exception) -> None:
        log.debug("game window lost: %r", exc)
        self.close()
