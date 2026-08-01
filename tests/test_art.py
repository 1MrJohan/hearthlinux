"""Regression tests for the three art-fetch failure modes fixed together:

* A transient failure (timeout, connection error) used to poison a card's art
  forever for the process's life, identically to a genuine 404. It is now
  remembered only for ``_RETRY_AFTER`` seconds.
* Two concurrent callers wanting the same uncached card_id/kind used to race
  two downloads onto the same destination file. They now share one in-flight
  fetch.
* A response that isn't actually PNG/JPEG bytes (e.g. a CDN error page served
  with a 200 status) used to get cached to disk as if it were valid art.

Every test redirects the module's directory constants into tmp_path and
clears the in-memory `_failed` / `_inflight` state, since both are shared
process-wide state that must not leak between tests.
"""

from __future__ import annotations

import asyncio
import urllib.error

import pytest

from bgtracker.data import art

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
HTML_ERROR_BYTES = b"<html><body>404 not found</body></html>"


@pytest.fixture(autouse=True)
def _isolated_art_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "CROP_DIR", tmp_path / "crops")
    monkeypatch.setattr(art, "CARD_DIR", tmp_path / "cards")
    monkeypatch.setattr(art, "_KINDS", {
        "crop": (tmp_path / "crops", ".jpg"),
        "render": (tmp_path / "cards", ".png"),
    })
    art._failed.clear()
    art._inflight.clear()
    yield
    art._failed.clear()
    art._inflight.clear()


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_transient_failure_is_retried_after_the_window(monkeypatch):
    """Timeout-like errors must not be cached as permanently as a 404."""
    calls = []

    def flaky_then_ok(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise TimeoutError("simulated network blip")
        return _FakeResponse(PNG_BYTES)

    monkeypatch.setattr(art.urllib.request, "urlopen", flaky_then_ok)

    async def run():
        first = await art.fetch_art("TEST_CARD", "crop")
        assert first is None
        assert len(calls) == 1

        # Still inside the retry window: must not hit the network again.
        second = await art.fetch_art("TEST_CARD", "crop")
        assert second is None
        assert len(calls) == 1

        # Fast-forward past the retry window: should attempt again and succeed.
        real_monotonic = art.time.monotonic
        monkeypatch.setattr(
            art.time, "monotonic", lambda: real_monotonic() + art._RETRY_AFTER + 1
        )
        third = await art.fetch_art("TEST_CARD", "crop")
        assert third is not None
        assert third.is_file()
        assert len(calls) == 2

    asyncio.run(run())


def test_concurrent_fetches_for_the_same_card_share_one_download(monkeypatch):
    """Two callers racing on the same uncached card must not fetch twice."""
    calls = []

    def slow_ok(req, timeout):
        calls.append(req.full_url)
        # Give the second coroutine a chance to start before this returns,
        # so the test actually exercises the overlap rather than two fast
        # sequential calls that never race.
        import time as _time

        _time.sleep(0.05)
        return _FakeResponse(PNG_BYTES)

    monkeypatch.setattr(art.urllib.request, "urlopen", slow_ok)

    async def run():
        a, b = await asyncio.gather(
            art.fetch_art("SAME_CARD", "crop"),
            art.fetch_art("SAME_CARD", "crop"),
        )
        assert a is not None and a == b
        assert len(calls) == 1

    asyncio.run(run())


def test_a_non_image_response_is_treated_as_a_miss_not_cached(monkeypatch, tmp_path):
    """An HTML error page served with 200 status must not become 'art'."""

    def returns_html(req, timeout):
        return _FakeResponse(HTML_ERROR_BYTES)

    monkeypatch.setattr(art.urllib.request, "urlopen", returns_html)

    async def run():
        result = await art.fetch_art("BOGUS_CARD", "crop")
        assert result is None
        assert not (tmp_path / "crops" / "BOGUS_CARD.jpg").exists()

    asyncio.run(run())
