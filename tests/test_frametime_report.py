"""The frametime reporter has to surface hitches, which means the slow tail.

A mean frame time averages a stutter away: one 90ms frame in a second of 60fps
moves the mean by about a millisecond and is plainly visible to the player.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "frametime-report.py"


def _module():
    spec = importlib.util.spec_from_file_location("frametime_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _module()


def test_reads_mangohud_csv_and_converts_microseconds_to_ms(tmp_path, mod):
    """MangoHud logs frametime in microseconds, with a system-info preamble
    line before the header row."""
    log = tmp_path / "hs.csv"
    log.write_text(
        "os,cpu,gpu,ram,kernel,driver\n"
        "Linux,i9-14900K,RTX,62GB,7.1.5,555\n"
        "fps,frametime,cpu_load,gpu_load,elapsed\n"
        "60,16667,20,50,1000\n"
        "58,17241,21,51,2000\n"
        "11,90000,80,52,3000\n"
    )
    assert mod.read_frametimes(log) == pytest.approx([16.667, 17.241, 90.0])


def test_low_is_the_mean_of_the_slowest_fraction(mod):
    # 100 frames: ninety-nine at 10ms and one at 100ms. The worst 1% is that
    # single frame, so the 1% low is the hitch itself, not a diluted average.
    frames = [10.0] * 99 + [100.0]
    assert mod.low(frames, 0.01) == pytest.approx(100.0)
    assert mod.low(frames, 0.50) == pytest.approx(11.8, abs=0.1)


def test_low_always_counts_at_least_one_frame(mod):
    """0.1% of a 200-frame capture rounds to zero frames; a reporter that
    returned a mean of nothing would print nan and look like a parse bug."""
    assert mod.low([10.0] * 199 + [50.0], 0.001) == pytest.approx(50.0)


def test_report_names_the_numbers_and_the_sample_size(mod):
    text = mod.report([10.0] * 99 + [100.0])
    assert "100 frames" in text
    assert "1% low" in text
    assert "0.1% low" in text
    assert "max" in text


def test_a_log_with_no_frames_reports_that_rather_than_dividing_by_zero(mod):
    assert "no frames" in mod.report([])
