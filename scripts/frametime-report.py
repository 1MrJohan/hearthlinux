#!/usr/bin/env python3
"""Report the slow tail of a MangoHud frametime log.

Capturing is a manual step, because MangoHud reads its config from the
environment at game launch and the tracker cannot set it retroactively. Put
this in the Steam launch options (or export it before starting the game any
other way):

    MANGOHUD=1 MANGOHUD_CONFIG=output_folder=/tmp/ft,toggle_logging=F2

then press F2 to start a capture and F2 again to stop it. Capture the same
thing before and after a change: several recruit phases and several
shop->combat transitions, in one continuous log each.

    scripts/frametime-report.py /tmp/ft/*.csv

Mean FPS is deliberately not reported. One 90ms frame in a second of 60fps
moves the mean by about a millisecond and is plainly visible to the player, so
the mean is the one number guaranteed to hide a hitch. The 0.1% low is the one
that corresponds to what you feel.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# MangoHud writes frametimes in microseconds.
_US_PER_MS = 1000.0

# A frame slower than this is not a frame, it is a load screen or a paused
# capture; including them makes the tail meaningless.
_IMPLAUSIBLE_MS = 2000.0


def read_frametimes(path: Path) -> list[float]:
    """Frame times in milliseconds, from a MangoHud CSV log.

    The file opens with a system-info preamble (a header row and one data row)
    before the real header, so the columns are found by name rather than by
    position.
    """
    rows = list(csv.reader(Path(path).read_text().splitlines()))
    for index, row in enumerate(rows):
        if "frametime" in row:
            column = row.index("frametime")
            break
    else:
        raise ValueError(f"{path}: no 'frametime' column found")

    out: list[float] = []
    for row in rows[index + 1:]:
        if len(row) <= column:
            continue
        try:
            value = float(row[column]) / _US_PER_MS
        except ValueError:
            continue
        if 0 < value < _IMPLAUSIBLE_MS:
            out.append(value)
    return out


def low(frametimes_ms: list[float], fraction: float) -> float:
    """Mean of the slowest `fraction` of frames, in milliseconds.

    At least one frame always counts: 0.1% of a 200-frame capture rounds to
    zero, and a mean of nothing prints as nan and reads like a parse bug.
    """
    if not frametimes_ms:
        return 0.0
    count = max(1, round(len(frametimes_ms) * fraction))
    worst = sorted(frametimes_ms, reverse=True)[:count]
    return sum(worst) / len(worst)


def report(frametimes_ms: list[float]) -> str:
    if not frametimes_ms:
        return "no frames in log"
    ordered = sorted(frametimes_ms)
    median = ordered[len(ordered) // 2]
    return (
        f"{len(frametimes_ms)} frames\n"
        f"  median    {median:7.2f} ms  ({1000 / median:5.1f} fps)\n"
        f"  1% low    {low(frametimes_ms, 0.01):7.2f} ms\n"
        f"  0.1% low  {low(frametimes_ms, 0.001):7.2f} ms\n"
        f"  max       {ordered[-1]:7.2f} ms"
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for path in argv:
        print(f"== {path}")
        print(report(read_frametimes(Path(path))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
