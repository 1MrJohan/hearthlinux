# Simulator CPU Isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the combat simulator from making Hearthstone hitch, by denying it the CPU cores the game runs on and by not generating load nobody is waiting for.

**Architecture:** The Node sidecar is spawned under a `preexec_fn` that applies `SCHED_IDLE`, `nice 19`, and CPU affinity to the machine's efficiency cores — set before `exec` because affinity is per-thread and inherited at thread creation, which is the only way to cover worker threads the sidecar creates later. Separately, the shop forecast stops fanning out across the whole worker pool and gets a reserved lane at the end of it, and catch-up replay stops re-simulating combats that already happened.

**Tech Stack:** Python 3.14 (system interpreter via `--system-site-packages` venv), asyncio, Node.js sidecar (`@firestone-hs/simulate-bgs-battle`), SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-sim-cpu-isolation-design.md`

## Global Constraints

- Python is `.venv/bin/python`. The venv uses `--system-site-packages` and the system interpreter (3.14); never recreate it without that flag.
- **Run the whole suite after every task**: `.venv/bin/python -m pytest tests/`. It is ~320 tests in ~23s. There is no reason to run a subset and call it done.
- There is no linter. `pyflakes` is in the venv for a quick check.
- Adding a config option means **two edits**: a `Config` field in `bgtracker/config.py` *and* a `Setting` in `bgtracker/settings.py`. `tests/test_settings_schema.py` fails if you do only one, or if `config.example.toml` drifts.
- Regenerate the example file with:
  `.venv/bin/python -c "from bgtracker.settings import example_toml; open('config.example.toml','w').write(example_toml())"`
- Adding a **column** to `combats` needs an `_ADDED_COMBAT_COLUMNS` entry, never a `_SCHEMA` edit. **This plan adds no columns** — only changes to statements.
- Commit messages explain the failure, not the diff: what was wrong, why the fix is shaped this way, what it was verified against. End with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Work happens on branch `sim-cpu-isolation`, which already exists and holds the spec commit.
- The working tree has **unrelated uncommitted work** in `CLAUDE.md`, `bgtracker/data/art.py`, and `tests/test_art.py`. Do not stage them. Always `git add` explicit paths, never `git add -A`.
- Sidecar worker stdout is captured, never inherited — the parent speaks JSON-lines on stdout and one stray `console.log` corrupts the protocol.

---

### Task 1: Frametime reporter

The only instrument that can answer "did the game stop hitching". Mean FPS averages away exactly the thing being fixed, so this reports the slow tail.

**Files:**
- Create: `scripts/frametime-report.py`
- Test: `tests/test_frametime_report.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `read_frametimes(path: Path) -> list[float]` (milliseconds), `low(frametimes_ms: list[float], fraction: float) -> float`, `report(frametimes_ms: list[float]) -> str`. Later tasks only run the script; no Python imports it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_frametime_report.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_frametime_report.py -v`
Expected: FAIL — `FileNotFoundError` / `spec_from_file_location` returns None, because `scripts/frametime-report.py` does not exist.

- [ ] **Step 3: Write the implementation**

Create `scripts/frametime-report.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_frametime_report.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: PASS.

- [ ] **Step 6: Make the script executable and commit**

```bash
chmod +x scripts/frametime-report.py
git add scripts/frametime-report.py tests/test_frametime_report.py
git commit -m "$(cat <<'EOF'
Add a frametime reporter for before/after hitch measurement

The tracker is believed to make the game hitch, and nothing in the repo can
see that. Mean FPS is the one number guaranteed to hide it: a single 90ms
frame inside a second of 60fps moves the mean about a millisecond and is
plainly visible to the player. This reports the slow tail instead — median,
1% low, 0.1% low and max — from a MangoHud CSV.

Capture stays manual because MangoHud reads its config from the environment at
game launch, which the tracker cannot set retroactively; the invocation is in
the script's docstring.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Baseline capture (human step)

**No code.** This is the measurement the rest of the plan is judged against, and it has to happen before anything changes. If you would rather batch all the measuring, Tasks 3-5 and 7-9 can be implemented first — but then keep the tracker's `sim_cpu_policy` at `off` until a baseline exists, or there is nothing to compare to.

- [ ] **Step 1: Set up MangoHud capture**

Add to the Hearthstone launch environment (Steam launch options, before the `steam-launch.sh` wrapper, or exported in the shell that starts the game):

```
MANGOHUD=1 MANGOHUD_CONFIG=output_folder=/tmp/ft,toggle_logging=F2
```

- [ ] **Step 2: Capture with the tracker running, current code**

Play one game with the tracker up. Press F2 at the start of a recruit phase, play through several recruit phases and several shop→combat transitions, press F2 to stop.

- [ ] **Step 3: Capture with the tracker not running**

Same again with the tracker killed, as the control. Without it there is no way to tell a tracker-caused hitch from one the game makes on its own.

- [ ] **Step 4: Record the numbers**

```bash
scripts/frametime-report.py /tmp/ft/*.csv
```

Fill in the first two rows of the Measurements table at the end of this plan file.

- [ ] **Step 5: Commit the measurements**

```bash
git add docs/superpowers/plans/2026-07-28-sim-cpu-isolation.md
git commit -m "$(cat <<'EOF'
Record baseline frametimes with and without the tracker

The control matters as much as the sample: without a tracker-stopped capture
there is no way to tell a hitch the tracker causes from one the game makes on
its own, and the whole design is aimed at the first.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Find the efficiency cores

**Files:**
- Create: `bgtracker/sim/cpu.py`
- Test: `tests/test_sim_cpu.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `efficiency_cpus(root: Path = SYSFS_CPU) -> frozenset[int] | None`, and the module constant `SYSFS_CPU: Path`. Task 4 imports both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_cpu.py`:

```python
"""Finding the cores the game does not want.

Hybrid Intel parts pair fast P-cores with slower E-cores. The kernel sometimes
says which is which outright and sometimes does not — the author's does not —
so the frequency fallback is load-bearing rather than theoretical.
"""

from __future__ import annotations

from bgtracker.sim.cpu import efficiency_cpus


def _cpu(root, index, max_freq=None):
    d = root / f"cpu{index}"
    d.mkdir(parents=True)
    if max_freq is not None:
        (d / "cpufreq").mkdir()
        (d / "cpufreq" / "cpuinfo_max_freq").write_text(f"{max_freq}\n")


def test_prefers_the_kernel_saying_so(tmp_path):
    types = tmp_path / "types" / "intel_atom"
    types.mkdir(parents=True)
    (types / "cpulist").write_text("16-19,24\n")
    # Frequencies that would disagree, to prove which source wins.
    for i in range(4):
        _cpu(tmp_path, i, 5_700_000)
    assert efficiency_cpus(tmp_path) == frozenset({16, 17, 18, 19, 24})


def test_falls_back_to_the_slowest_frequency_tier(tmp_path):
    """The author's machine: three tiers, because Turbo Boost Max favours two
    P-cores with a higher ceiling than their siblings. Only the *lowest* tier
    is efficiency silicon — taking everything below the top would exile the
    simulator onto the P-cores it is supposed to avoid."""
    for i in range(0, 14):
        _cpu(tmp_path, i, 5_700_000)
    for i in (14, 15):
        _cpu(tmp_path, i, 6_000_000)
    for i in range(16, 32):
        _cpu(tmp_path, i, 4_400_000)
    assert efficiency_cpus(tmp_path) == frozenset(range(16, 32))


def test_a_non_hybrid_cpu_has_no_efficiency_cores(tmp_path):
    for i in range(8):
        _cpu(tmp_path, i, 4_200_000)
    assert efficiency_cpus(tmp_path) is None


def test_missing_cpufreq_is_not_an_error(tmp_path):
    """Kernels without cpufreq exposed, and VMs, must degrade to 'no idea'
    rather than raising — this runs on the path that spawns the sidecar."""
    for i in range(4):
        _cpu(tmp_path, i)
    assert efficiency_cpus(tmp_path) is None


def test_an_empty_tree_is_not_an_error(tmp_path):
    assert efficiency_cpus(tmp_path) is None


def test_an_empty_cpulist_falls_through_to_frequencies(tmp_path):
    types = tmp_path / "types" / "intel_atom"
    types.mkdir(parents=True)
    (types / "cpulist").write_text("\n")
    for i in range(2):
        _cpu(tmp_path, i, 5_000_000)
    for i in (2, 3):
        _cpu(tmp_path, i, 3_000_000)
    assert efficiency_cpus(tmp_path) == frozenset({2, 3})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sim_cpu.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bgtracker.sim.cpu'`.

- [ ] **Step 3: Write the implementation**

Create `bgtracker/sim/cpu.py`:

```python
"""Which CPUs the simulator is allowed to run on.

Hybrid Intel parts (Alder Lake onward) pair fast P-cores with slower E-cores.
The game wants the P-cores and cannot be asked to share them; the simulator
does not care which cores it gets so long as it gets some. Finding the E-cores
is therefore the whole job of this module.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

SYSFS_CPU = Path("/sys/devices/system/cpu")

# What kernels that expose hybrid topology directly call the efficiency cluster.
_EFFICIENCY_TYPE = "intel_atom"


def _parse_cpulist(text: str) -> frozenset[int]:
    """Expand a kernel cpulist ("0-7,16,18-19") into CPU numbers."""
    cpus: set[int] = set()
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            low, high = part.split("-", 1)
            cpus.update(range(int(low), int(high) + 1))
        else:
            cpus.add(int(part))
    return frozenset(cpus)


def _from_types(root: Path) -> frozenset[int] | None:
    try:
        cpus = _parse_cpulist((root / "types" / _EFFICIENCY_TYPE / "cpulist").read_text())
    except (OSError, ValueError):
        return None
    return cpus or None


def _from_max_freq(root: Path) -> frozenset[int] | None:
    tiers: dict[int, set[int]] = {}
    for entry in root.glob("cpu[0-9]*"):
        index = entry.name[3:]
        if not index.isdigit():
            continue
        try:
            freq = int((entry / "cpufreq" / "cpuinfo_max_freq").read_text())
        except (OSError, ValueError):
            continue
        tiers.setdefault(freq, set()).add(int(index))
    if len(tiers) < 2:
        # One tier is a uniform CPU; none readable is a kernel that will not
        # say. Both mean there is no subset worth exiling the simulator to.
        return None
    return frozenset(tiers[min(tiers)])


def efficiency_cpus(root: Path = SYSFS_CPU) -> frozenset[int] | None:
    """The efficiency CPUs, or None when this is not a hybrid machine.

    `types/intel_atom/cpulist` is the kernel saying so outright and is
    preferred, but it is absent on plenty of kernels running hybrid silicon —
    including the author's — so the fallback groups CPUs by `cpuinfo_max_freq`
    and takes the slowest group.

    Grouping by exact frequency and keeping only the *lowest* group is what
    keeps Turbo Boost Max favoured cores on the performance side: on a 14900K
    they report 6.0 GHz beside their siblings' 5.7, so "not the top tier" would
    wrongly include fourteen P-cores.

    `cpu_capacity` is not usable for this — on x86 the kernel reports 1024 for
    every CPU regardless of core type.
    """
    return _from_types(root) or _from_max_freq(root)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sim_cpu.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Sanity-check against the real machine**

Run: `.venv/bin/python -c "from bgtracker.sim.cpu import efficiency_cpus; c=efficiency_cpus(); print(len(c) if c else None, sorted(c) if c else None)"`
Expected on the author's i9-14900K: `16 [16, 17, ..., 31]`. A different answer here means the frequency tiers were read wrong and the rest of the plan is aimed at the wrong cores.

- [ ] **Step 6: Run the whole suite and commit**

```bash
.venv/bin/python -m pytest tests/
git add bgtracker/sim/cpu.py tests/test_sim_cpu.py
git commit -m "$(cat <<'EOF'
Identify the efficiency cores the simulator can be exiled to

Hybrid Intel parts pair fast P-cores with slower E-cores, and the game wants
the P-cores. Kernels that expose types/intel_atom/cpulist say which is which
outright; the author's does not, so the frequency fallback is the path that
actually runs and gets the tests.

Grouping by exact cpuinfo_max_freq and keeping only the lowest group is the
part that is easy to get wrong: Turbo Boost Max favours two cores on a 14900K
with a 6.0 GHz ceiling beside their siblings' 5.7, so "anything below the top
tier" would have classified fourteen P-cores as efficiency silicon and pinned
the simulator onto exactly the cores this is meant to keep clear.

cpu_capacity is not usable — on x86 the kernel reports 1024 for every CPU.

Verified against the real machine: 16 cores, 16-31.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: The deference `preexec_fn`

**Files:**
- Modify: `bgtracker/sim/cpu.py`
- Test: `tests/test_sim_cpu.py`

**Interfaces:**
- Consumes: `efficiency_cpus`, `SYSFS_CPU` from Task 3.
- Produces: `spawn_preexec(policy: str = "auto", root: Path = SYSFS_CPU) -> Callable[[], None] | None`. Task 5 passes the result straight to `asyncio.create_subprocess_exec(preexec_fn=...)`, which accepts `None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sim_cpu.py`:

```python
import os
import subprocess
import sys

import pytest

from bgtracker.sim.cpu import spawn_preexec


def test_policy_off_returns_nothing_to_apply():
    """None is a valid preexec_fn, so the caller passes it through either way
    rather than branching at the spawn."""
    assert spawn_preexec("off") is None


def test_applies_affinity_scheduler_and_nice(tmp_path, monkeypatch):
    for i in range(2):
        (tmp_path / f"cpu{i}" / "cpufreq").mkdir(parents=True)
        (tmp_path / f"cpu{i}" / "cpufreq" / "cpuinfo_max_freq").write_text("5000000")
    for i in (2, 3):
        (tmp_path / f"cpu{i}" / "cpufreq").mkdir(parents=True)
        (tmp_path / f"cpu{i}" / "cpufreq" / "cpuinfo_max_freq").write_text("3000000")

    seen = {}
    monkeypatch.setattr(os, "sched_setaffinity", lambda pid, cpus: seen.update(affinity=(pid, set(cpus))))
    monkeypatch.setattr(os, "sched_setscheduler", lambda pid, policy, param: seen.update(policy=(pid, policy)))
    monkeypatch.setattr(os, "nice", lambda inc: seen.update(nice=inc))

    spawn_preexec("auto", tmp_path)()

    assert seen["affinity"] == (0, {2, 3})
    assert seen["policy"] == (0, os.SCHED_IDLE)
    assert seen["nice"] == 19


def test_never_raises_when_the_kernel_refuses(tmp_path, monkeypatch):
    """An exception inside a preexec_fn surfaces in the parent as a
    SubprocessError and would take the sidecar down. Failing to lower our own
    priority is not a reason to have no odds at all."""
    for i in range(2):
        (tmp_path / f"cpu{i}" / "cpufreq").mkdir(parents=True)
        (tmp_path / f"cpu{i}" / "cpufreq" / "cpuinfo_max_freq").write_text("5000000")
    (tmp_path / "cpu2" / "cpufreq").mkdir(parents=True)
    (tmp_path / "cpu2" / "cpufreq" / "cpuinfo_max_freq").write_text("3000000")

    def boom(*_a, **_k):
        raise OSError("refused")

    monkeypatch.setattr(os, "sched_setaffinity", boom)
    monkeypatch.setattr(os, "sched_setscheduler", boom)
    monkeypatch.setattr(os, "nice", boom)

    spawn_preexec("auto", tmp_path)()   # must not raise


def test_a_non_hybrid_machine_still_gets_the_scheduler_settings(tmp_path, monkeypatch):
    """Affinity is the part that needs a hybrid CPU. Idle priority is not, and
    it is the whole fix on a machine where every core is the same."""
    for i in range(4):
        (tmp_path / f"cpu{i}" / "cpufreq").mkdir(parents=True)
        (tmp_path / f"cpu{i}" / "cpufreq" / "cpuinfo_max_freq").write_text("4000000")

    seen = {}
    monkeypatch.setattr(os, "sched_setaffinity", lambda *a: seen.update(affinity=True))
    monkeypatch.setattr(os, "sched_setscheduler", lambda pid, policy, param: seen.update(policy=policy))
    monkeypatch.setattr(os, "nice", lambda inc: seen.update(nice=inc))

    spawn_preexec("auto", tmp_path)()

    assert "affinity" not in seen, "nothing to pin to on a uniform CPU"
    assert seen["policy"] == os.SCHED_IDLE
    assert seen["nice"] == 19


def test_it_really_works_on_this_kernel():
    """Out of process, because applying it in the test runner would leave
    pytest itself at SCHED_IDLE and nice 19 for the rest of the suite.

    The monkeypatched tests above prove the guard logic; this proves the
    syscalls are actually accepted by the kernel we run on.
    """
    code = (
        "import os, sys;"
        "sys.path.insert(0, %r);"
        "from bgtracker.sim.cpu import spawn_preexec, efficiency_cpus;"
        "spawn_preexec('auto')();"
        "print(os.sched_getscheduler(0), len(os.sched_getaffinity(0)),"
        " len(efficiency_cpus() or []))"
    ) % str(__import__("pathlib").Path(__file__).resolve().parent.parent)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    policy, affinity, efficiency = (int(n) for n in out.stdout.split())
    assert policy == os.SCHED_IDLE
    if efficiency:
        assert affinity == efficiency, "affinity should be exactly the efficiency cores"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sim_cpu.py -v`
Expected: FAIL with `ImportError: cannot import name 'spawn_preexec'`.

- [ ] **Step 3: Write the implementation**

Append to `bgtracker/sim/cpu.py`:

```python
from collections.abc import Callable

# nice(19) is the fallback for a kernel that refuses sched_setscheduler;
# SCHED_IDLE ignores nice entirely, so on a working kernel it does nothing.
_NICE = 19


def spawn_preexec(policy: str = "auto", root: Path = SYSFS_CPU) -> Callable[[], None] | None:
    """A `preexec_fn` that makes the sidecar defer to whatever else is running.

    Returns None when the policy is off, which `create_subprocess_exec` accepts
    as "no preexec" — so the caller passes the result straight through instead
    of branching at the spawn.

    **Why before exec rather than on the running process.** Affinity and
    scheduling policy are per-*thread* on Linux and inherited at thread
    creation, not applied retroactively to a process. The sidecar's workers are
    created at module load, before the parent has even read the `ready` line,
    and `retireWorker` spawns more of them mid-game when the watchdog kills a
    hung one. Setting the mask here is the only point that covers every thread,
    present and future, with no race and no walk of /proc/<pid>/task.

    Safe because this process is deliberately single-threaded — threads in the
    parent are `preexec_fn`'s documented hazard, and the three calls below are
    thin syscall wrappers that allocate nothing.

    **Why SCHED_IDLE and not merely nice.** The simulator already degrades
    gracefully under starvation: `maxAcceptableDuration` truncates the run and
    the sidecar pools whatever shards finished, so scheduler pressure costs
    sample size — a wider `margin`, recorded in `sims_run` — never correctness.
    That is what licenses the aggressive setting.
    """
    if policy != "auto":
        return None
    cpus = efficiency_cpus(root)
    if cpus:
        log.info("simulator pinned to %d efficiency cores at idle priority", len(cpus))
    else:
        log.info("simulator at idle priority (no efficiency cores to pin to)")

    def apply() -> None:
        # Nothing here may raise: an exception inside a preexec_fn surfaces in
        # the parent as a SubprocessError and would take the sidecar down.
        # Failing to lower our own priority is not a reason to have no odds.
        if cpus:
            try:
                os.sched_setaffinity(0, cpus)
            except OSError:
                pass
        try:
            os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
        except (OSError, ValueError, AttributeError):
            pass
        try:
            os.nice(_NICE)
        except OSError:
            pass

    return apply
```

Add `import os` to the module's imports at the top.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sim_cpu.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Run the whole suite and commit**

```bash
.venv/bin/python -m pytest tests/
git add bgtracker/sim/cpu.py tests/test_sim_cpu.py
git commit -m "$(cat <<'EOF'
Build the preexec_fn that makes the sidecar defer

Applied before exec rather than to the running process because affinity and
scheduling policy are per-thread on Linux and inherited at thread creation.
The sidecar's workers exist before the parent has read the ready line, and
retireWorker spawns more mid-game when the watchdog kills a hung one, so this
is the only point that covers every thread without a race or a walk of
/proc/<pid>/task. Safe because the parent is deliberately single-threaded.

Nothing in the applier may raise: an exception inside a preexec_fn comes back
to the parent as a SubprocessError, and failing to lower our own priority is
not a reason to have no odds at all. Each of the three calls is guarded
separately and a non-hybrid machine still gets idle priority.

Tested both ways — monkeypatched in-process for the guard logic, and out of
process for the syscalls, because applying SCHED_IDLE and nice 19 in the test
runner would leave pytest itself there for the rest of the suite.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Wire the policy into the sidecar spawn

Completes A. After this task the fix is live and measurable.

**Files:**
- Modify: `bgtracker/config.py` (add field to `Config`)
- Modify: `bgtracker/settings.py` (add `Setting`, simulator section)
- Modify: `config.example.toml` (regenerated)
- Modify: `bgtracker/sim/client.py` (`__init__`, `_ensure_proc`, `reconfigure_workers`)
- Modify: `bgtracker/__main__.py` (`start_sim`, the `SIM_RESPAWN` subscription)
- Test: `tests/test_settings_apply.py`

**Interfaces:**
- Consumes: `spawn_preexec` from Task 4.
- Produces: `SimClient(..., cpu_policy: str = "auto")` with a `cpu_policy` attribute, and `SimClient.reconfigure(workers: int, cpu_policy: str) -> bool` replacing `reconfigure_workers`. Task 10 adds a `shop_workers` attribute alongside `cpu_policy`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settings_apply.py`:

```python
def _stubbed_client(**kwargs):
    """A SimClient whose respawn is recorded instead of performed.

    Spawning for real would build a ~350MB card DB per worker, which is the
    cost `reconfigure` exists to avoid paying twice.
    """
    from bgtracker.sim.client import SimClient

    sim = SimClient(**kwargs)
    sim.spawned = []

    async def fake_stop():
        sim.spawned.append("stop")

    async def fake_ensure():
        sim.spawned.append("start")

    sim._stop = fake_stop
    sim._ensure_proc = fake_ensure
    return sim


def test_changing_the_cpu_policy_respawns_the_sidecar():
    """The mask is set at spawn, so the policy genuinely cannot change without
    a restart — which is why it rides SIM_RESPAWN rather than SIM."""
    async def run():
        sim = _stubbed_client(workers=2, cpu_policy="auto")
        moved = await sim.reconfigure(workers=2, cpu_policy="off")
        return moved, sim.cpu_policy, sim.spawned

    moved, policy, spawned = asyncio.run(run())
    assert moved is True
    assert policy == "off"
    assert spawned == ["stop", "start"]


def test_changing_the_worker_count_still_respawns():
    async def run():
        sim = _stubbed_client(workers=2, cpu_policy="auto")
        moved = await sim.reconfigure(workers=6, cpu_policy="auto")
        return moved, sim.workers, sim.spawned

    moved, workers, spawned = asyncio.run(run())
    assert moved is True
    assert workers == 6
    assert spawned == ["stop", "start"]


def test_reconfigure_is_a_no_op_when_nothing_moved():
    """Respawning costs a multi-second card-DB rebuild per worker. Doing it on
    an unchanged value would charge that to whatever combat came next."""
    async def run():
        sim = _stubbed_client(workers=4, cpu_policy="auto")
        moved = await sim.reconfigure(workers=4, cpu_policy="auto")
        return moved, sim.spawned

    assert asyncio.run(run()) == (False, [])
```

Add `import asyncio` to the file's imports if it is not already there.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settings_apply.py -v`
Expected: FAIL — `TypeError: SimClient.__init__() got an unexpected keyword argument 'cpu_policy'`.

- [ ] **Step 3: Add the Config field**

In `bgtracker/config.py`, in the `# -- combat simulator` block, after `sim_workers`:

```python
    # Keep the simulator off the cores the game is drawing on. "auto" runs the
    # sidecar at SCHED_IDLE / nice 19 and, on a hybrid CPU, pins it to the
    # efficiency cores. "off" is the old behaviour, for bisecting.
    sim_cpu_policy: str = "auto"
```

- [ ] **Step 4: Add the Setting**

In `bgtracker/settings.py`, in the `# -- simulator` block, after the `sim_workers` entry:

```python
    Setting(
        "sim_cpu_policy", "simulator", "choice", "auto",
        "Keep the simulator off the game's cores",
        "Runs the sidecar at idle priority, and on a hybrid CPU pins it to the "
        "efficiency cores so it cannot compete with the game for the fast ones. "
        "Odds take longer to firm up; a first number still arrives in about a "
        "tenth of a second. Turn off to bisect a performance problem. Changing "
        "it restarts the sidecar.",
        channel=SIM_RESPAWN, choices=("auto", "off"),
    ),
```

- [ ] **Step 5: Regenerate the example file**

```bash
.venv/bin/python -c "from bgtracker.settings import example_toml; open('config.example.toml','w').write(example_toml())"
```

- [ ] **Step 6: Wire it into SimClient**

In `bgtracker/sim/client.py`, add the import beside the others:

```python
from bgtracker.sim.cpu import spawn_preexec
```

Change `__init__` to accept and store the policy:

```python
    def __init__(
        self,
        sidecar_dir: Path = SIDECAR_DIR,
        timeout: float = 6.0,
        sims: int = 8000,
        workers: int = 0,
        cpu_policy: str = "auto",
    ):
        self.sidecar_dir = sidecar_dir
        self.timeout = timeout
        self.sims = sims
        # 0 leaves the choice to the sidecar's own default.
        self.workers = workers
        self.cpu_policy = cpu_policy
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
```

In `_ensure_proc`, add the `preexec_fn` argument to the spawn:

```python
            self._proc = await asyncio.create_subprocess_exec(
                "node",
                str(self.sidecar_dir / "server.mjs"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                # Applied in the child between fork and exec, so every worker
                # thread the sidecar creates — including ones retireWorker
                # spawns mid-game — inherits it. None when the policy is off.
                preexec_fn=spawn_preexec(self.cpu_policy),
            )
```

Replace `reconfigure_workers` with `reconfigure`, keeping the docstring's rationale:

```python
    async def reconfigure(self, workers: int, cpu_policy: str) -> bool:
        """Restart the sidecar on new spawn-time settings. Returns whether it moved.

        Both of these reach the sidecar only at spawn — the worker count as an
        environment variable, the CPU policy as a `preexec_fn` — so they
        genuinely cannot change without a restart.

        **The lock is the point.** `_request` holds `self._lock` across its
        `readline()` await, so taking it here means a change queues behind any
        combat forecast already in flight instead of killing the process under
        it — which would raise "sidecar died mid-request" and leave that fight
        with no odds at all. Respawning eagerly inside the lock also pays the
        multi-second card-DB rebuild now, while the user is looking at the
        settings window, rather than charging it to the next combat's timeout.
        """
        if workers == self.workers and cpu_policy == self.cpu_policy:
            return False
        async with self._lock:
            self.workers = workers
            self.cpu_policy = cpu_policy
            await self._stop()
            await self._ensure_proc()
        return True
```

- [ ] **Step 7: Wire it into startup and the settings channel**

In `bgtracker/__main__.py`, `start_sim`:

```python
async def start_sim(cfg) -> SimClient | None:
    sim = SimClient(
        timeout=cfg.sim_timeout, sims=cfg.sim_count, workers=cfg.sim_workers,
        cpu_policy=cfg.sim_cpu_policy,
    )
```

and in `live()`, the `SIM_RESPAWN` subscription:

```python
        settings.subscribe(
            SIM_RESPAWN,
            lambda _keys: settings.spawn(
                sim.reconfigure(cfg.sim_workers, cfg.sim_cpu_policy)
            ),
        )
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settings_apply.py tests/test_settings_schema.py -v`
Expected: PASS.

- [ ] **Step 9: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: PASS. `test_sim_roundtrip.py` and `test_sim_watchdog.py` now spawn the sidecar under the real policy; if either got slower but still passes, that is the change working.

- [ ] **Step 10: Verify the running sidecar is actually pinned**

```bash
.venv/bin/python -m bgtracker doctor
# then, with a tracker running:
pgrep -f 'node.*server.mjs' | head -1 | xargs -I{} sh -c 'grep Cpus_allowed_list /proc/{}/status; chrt -p {}'
```
Expected: `Cpus_allowed_list: 16-31` and `SCHED_IDLE`. This is the check that proves the wiring, not just the unit.

- [ ] **Step 11: Commit**

```bash
git add bgtracker/config.py bgtracker/settings.py config.example.toml \
        bgtracker/sim/client.py bgtracker/__main__.py tests/test_settings_apply.py
git commit -m "$(cat <<'EOF'
Spawn the simulator sidecar at idle priority on the efficiency cores

Four CPU-bound worker threads at normal priority were competing with a game
that needs a frame every 16ms, and the two moments the author sees hitches are
exactly the two moments the simulator runs. Twelve percent utilisation is not
a capacity problem — a hitch is one preempted frame — so the fix is denying
the simulator the cores the game wants rather than finding it more.

reconfigure_workers becomes reconfigure(workers, cpu_policy): both reach the
sidecar only at spawn, one as an environment variable and one as a preexec_fn,
so both need the same restart and the same request lock to avoid killing the
process out from under a combat in flight.

Verified on the running sidecar, not just in tests: Cpus_allowed_list 16-31
and SCHED_IDLE.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `resim` reports wall-clock

Turns the existing regression harness into the throughput bench, so Tasks 7 and 11 have numbers to judge A and set defaults from.

**Files:**
- Modify: `bgtracker/history/resim.py` (`Row`, the `resim` loop, `_report`)
- Test: `tests/test_resim_report.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Row.sim_ms: float`, and `_report` output containing a wall-time line. Nothing later imports these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_resim_report.py`:

```python
"""The resim report doubles as the simulator's throughput bench.

It already replays stored boards through the live sidecar serially, which is
the benchmark loop. It just never said how long they took.
"""

from __future__ import annotations

from bgtracker.history.resim import Row, _report


def _row(then, now, ms, sims=8000):
    return Row(turn=5, opponent_hero="H", outcome="win", then=then, now=now,
               sims_run=sims, sim_ms=ms)


def test_report_includes_the_wall_time_distribution():
    text = _report([_row(50, 51, 100.0), _row(40, 41, 300.0), _row(30, 31, 2000.0)])
    assert "mean 800" in text or "mean 800.0" in text, text
    assert "max 2000" in text, text


def test_wall_time_is_reported_even_when_nothing_is_comparable():
    """A corpus with no stored predictions still benchmarks fine — the drift
    table is what needs a 'then', not the clock."""
    text = _report([_row(None, 51, 120.0)])
    assert "120" in text


def test_truncated_runs_are_still_called_out():
    # The existing accuracy guard must survive the addition.
    text = _report([_row(50, 51, 100.0, sims=1200)])
    assert "under 4000 trials" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_resim_report.py -v`
Expected: FAIL — `TypeError: Row.__init__() got an unexpected keyword argument 'sim_ms'`.

- [ ] **Step 3: Add the field and populate it**

In `bgtracker/history/resim.py`, add to `Row`:

```python
    sims_run: int = 0
    sim_ms: float = 0.0
```

In the `resim` loop, alongside `row.sims_run`:

```python
                    current = await sim.simulate(info)
                    row.now = current.won_percent
                    row.sims_run = current.sims_run
                    row.sim_ms = current.sim_ms
```

- [ ] **Step 4: Report it**

In `_report`, after the `lines = [...]` line and before the `if scored:` block:

```python
    timed = [r.sim_ms for r in results if r.sim_ms > 0]
    if timed:
        # This is the throughput bench: how long a real board costs under
        # whatever CPU policy and worker count the sidecar just ran with.
        ordered = sorted(timed)
        lines.append(
            f"  wall time: mean {sum(timed) / len(timed):.0f}ms, "
            f"median {ordered[len(ordered) // 2]:.0f}ms, max {ordered[-1]:.0f}ms"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resim_report.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Run the whole suite and commit**

```bash
.venv/bin/python -m pytest tests/
git add bgtracker/history/resim.py tests/test_resim_report.py
git commit -m "$(cat <<'EOF'
Report wall time from resim so it doubles as the throughput bench

resim already replays hundreds of stored boards through the live sidecar
serially, which is exactly the benchmark loop — it simply never said how long
they took. Moving the simulator to slower cores and giving the combat forecast
fewer workers both trade wall time for staying out of the game's way, and
neither trade can be judged without this.

The accuracy guard is unchanged: sims_run under 4000 is still called out, and
it is what says whether the slower configuration started truncating runs.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Checkpoint — measure A (human step)

- [ ] **Step 1: Capture frametimes with the new policy**

Same procedure as Task 2, with `sim_cpu_policy = "auto"` (the default). Play a game, capture several recruit phases and shop→combat transitions.

- [ ] **Step 2: Compare**

```bash
scripts/frametime-report.py /tmp/ft/*.csv
```
Compare 0.1% low and max against the Task 2 baselines. The tracker-running numbers should move toward the tracker-stopped control.

- [ ] **Step 3: Measure the cost**

```bash
.venv/bin/python -m bgtracker resim 60
```
Record the wall-time line and the truncation line. **`sims_run` under 8000 anywhere is the signal that matters** — 589 recorded combats are a flat 8000, so any truncation is new and is A costing accuracy rather than just latency.

- [ ] **Step 4: Record and commit**

Append both to the Measurements section. If the frametimes did not move, **stop and reassess** — the diagnosis was inference, not measurement, and B is aimed at the same mechanism. Say so rather than continuing on faith.

```bash
git add docs/superpowers/plans/2026-07-28-sim-cpu-isolation.md
git commit -m "Record frametimes and sim cost after scheduler isolation

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: History writes stop losing data

Must land before Task 9, which is what would otherwise start blanking predictions.

**Files:**
- Modify: `bgtracker/history/db.py` (`record_combat`, `end_game`)
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: nothing.
- Produces: no signature changes. `record_combat(game_id, snapshot, prediction, outcome)` and `end_game(game_id, placement, final_turn)` keep their signatures and gain the property that a `None` cannot overwrite a stored value.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history.py`:

```python
def test_re_recording_without_a_prediction_keeps_the_stored_one(tmp_path):
    """Catch-up replays a session from the top and re-records every combat.
    Once it stops simulating them, the second write carries prediction=None —
    and the calibration table is built from exactly those columns."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    snap = BoardSnapshot(turn=5, friendly=_board(1, 40), opponent=_board(2, 30))
    db.record_combat(game, snap, SimResult(
        won_percent=61, tied_percent=4, lost_percent=35,
        avg_damage_won=9, avg_damage_lost=7, sims_run=8000, sim_ms=412.0,
        lost_lethal_percent=3.0,
    ), "win")

    db.record_combat(game, snap, None, "win")

    row = db.conn.execute(
        "SELECT predicted_win, sims_run, sim_ms, predicted_lost_lethal,"
        " outcome FROM combats WHERE game_id=? AND turn=5", (game,)
    ).fetchone()
    assert row == (61, 8000, 412.0, 3.0, "win")
    db.close()


def test_re_recording_still_refreshes_the_boards(tmp_path):
    """Boards are projections of the same log, so the newer read wins — only
    the prediction columns are the ones a null must never blank."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    first = BoardSnapshot(turn=5, friendly=_board(1, 40), opponent=_board(2, 30))
    second = BoardSnapshot(turn=5, friendly=_board(1, 40), opponent=_board(2, 22))
    db.record_combat(game, first, None, None)
    db.record_combat(game, second, None, "loss")

    row = db.conn.execute(
        "SELECT opp_board, outcome FROM combats WHERE game_id=? AND turn=5", (game,)
    ).fetchone()
    assert json.loads(row[0])["health"] == 22
    assert row[1] == "loss"
    db.close()


def test_a_game_ends_once(tmp_path):
    """end_game stamped ended_at with _now() on every write, so replaying a
    finished session moved its end time to the replay's clock — 14 games in the
    author's database claim durations over three hours, their end times
    clustered seconds apart on the evening they were replayed."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    db.end_game(game, placement=3, final_turn=24)
    first = db.conn.execute("SELECT ended_at FROM games WHERE id=?", (game,)).fetchone()[0]

    db.end_game(game, placement=3, final_turn=24)
    again = db.conn.execute("SELECT ended_at FROM games WHERE id=?", (game,)).fetchone()[0]
    assert again == first


def test_re_ending_without_a_placement_keeps_the_recorded_one(tmp_path):
    """GameEnd is deferred until a placement tag appears, but finalize() can
    flush one carrying None. On catch-up that would blank a real placement."""
    db = HistoryDB(tmp_path / "h.db")
    game = db.start_game("2026-07-28T10:00:00")
    db.end_game(game, placement=2, final_turn=24)
    db.end_game(game, placement=None, final_turn=24)
    assert db.conn.execute(
        "SELECT placement FROM games WHERE id=?", (game,)
    ).fetchone()[0] == 2
    db.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_history.py -k "re_recording or ends_once or re_ending" -v`
Expected: FAIL — the prediction reads back as `None`, `ended_at` moves, and the placement is blanked.

- [ ] **Step 3: Make `record_combat` an upsert that cannot lose data**

In `bgtracker/history/db.py`, replace the `INSERT OR REPLACE` statement in `record_combat`:

```python
        # An upsert rather than INSERT OR REPLACE, because catch-up replays a
        # session from the top and re-records every combat in it. Once catch-up
        # stops simulating, that second write carries prediction=None — and
        # REPLACE would blank the very columns the calibration table is built
        # from. Boards and the ghost flag are projections of the same log and
        # overwrite freely; the prediction and the outcome are the ones a null
        # must never overwrite.
        self.conn.execute(
            "INSERT INTO combats (game_id, turn, opponent_hero, predicted_win,"
            " predicted_tie, predicted_loss, outcome, my_board, opp_board,"
            " sims_run, sim_ms, predicted_lost_lethal, opponent_is_ghost)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (game_id, turn) DO UPDATE SET"
            "  predicted_win  = COALESCE(excluded.predicted_win,  predicted_win),"
            "  predicted_tie  = COALESCE(excluded.predicted_tie,  predicted_tie),"
            "  predicted_loss = COALESCE(excluded.predicted_loss, predicted_loss),"
            "  sims_run       = COALESCE(excluded.sims_run,       sims_run),"
            "  sim_ms         = COALESCE(excluded.sim_ms,         sim_ms),"
            "  predicted_lost_lethal ="
            "    COALESCE(excluded.predicted_lost_lethal, predicted_lost_lethal),"
            "  outcome        = COALESCE(excluded.outcome,        outcome),"
            "  opponent_hero  = COALESCE(excluded.opponent_hero,  opponent_hero),"
            "  my_board          = excluded.my_board,"
            "  opp_board         = excluded.opp_board,"
            "  opponent_is_ghost = excluded.opponent_is_ghost",
            (
                game_id,
                snapshot.turn,
                snapshot.opponent.hero_card_id if snapshot.opponent else None,
                prediction.won_percent if prediction else None,
                prediction.tied_percent if prediction else None,
                prediction.lost_percent if prediction else None,
                outcome,
                _board_json(snapshot.friendly),
                _board_json(snapshot.opponent),
                # How much sample the number is actually backed by — without it
                # a truncated run is indistinguishable from a full one.
                prediction.sims_run if prediction else None,
                prediction.sim_ms if prediction else None,
                prediction.lost_lethal_percent if prediction else None,
                1 if is_ghost(snapshot.opponent) else 0,
            ),
        )
        self.conn.commit()
```

- [ ] **Step 4: Make `end_game` idempotent**

Replace `end_game`:

```python
    def end_game(self, game_id: int, placement: int | None, final_turn: int) -> None:
        # A game ends once, and the first time it was seen to end is the true
        # one. This used to be a plain assignment, so re-recording a finished
        # game on catch-up stamped ended_at with the replay's clock: 14 games
        # in the author's database claim durations over three hours, their end
        # times clustered seconds apart on the evening they were replayed.
        # Harmless only by luck — review.py groups periods on started_at, which
        # start_game preserves, and nothing reads ended_at at all.
        #
        # placement gets the same treatment for the same reason: GameEnd is
        # deferred until a placement tag appears, but finalize() can flush one
        # carrying None, which would blank a real result.
        self.conn.execute(
            "UPDATE games SET ended_at = COALESCE(ended_at, ?),"
            " placement = COALESCE(?, placement), final_turn = ?"
            " WHERE id = ?",
            (_now(), placement, final_turn, game_id),
        )
        self.conn.commit()
```

`final_turn` stays a plain assignment: it is derived from the same log every time and a replay reproduces it, so there is no null to guard against.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_history.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite and commit**

```bash
.venv/bin/python -m pytest tests/
git add bgtracker/history/db.py tests/test_history.py
git commit -m "$(cat <<'EOF'
Stop a re-record from blanking what was already recorded

record_combat was INSERT OR REPLACE against UNIQUE (game_id, turn) and
start_game resumes an existing row by log_id, so a catch-up replay overwrites
every combat in the session. That is harmless while catch-up re-predicts them
with an equally valid number, and destructive the moment it stops — which is
the next commit. The calibration table is built from exactly those columns.

The sibling was already corrupting data: end_game assigned ended_at = _now()
unconditionally, so replaying a finished session moved its end time to the
replay's clock. Fourteen games in the author's database claim durations over
three hours, their end times clustered seconds apart on one evening. It is
harmless only by luck — review.py groups periods on started_at, which
start_game preserves, and nothing reads ended_at at all. Placement gets the
same guard, because GameEnd is deferred until a placement tag appears and
finalize() can flush one carrying None.

The fourteen existing rows are left alone: there is no source of truth to
repair them from, and a migration that guessed would be worse than a value
nothing reads.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Catch-up stops re-simulating history

**Files:**
- Modify: `bgtracker/app.py` (`Pipeline.handle`, `Pipeline._simulate`)
- Modify: `bgtracker/__main__.py` (`live()` tail loop)
- Test: `tests/test_shop_forecast.py`

**Interfaces:**
- Consumes: the data safety from Task 8.
- Produces: `Pipeline.handle(events, historical: bool = False)`. Task 10 does not touch it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shop_forecast.py`:

```python
def test_catch_up_does_not_simulate_combats_that_already_happened():
    """The tailer reads each session log from the top, so a restart mid-session
    replays every combat in it. Each one used to cost a full 8000-trial run,
    serialized, while the player is in a game."""
    async def run():
        pipe, sim, _ = _pipeline()
        snap = BoardSnapshot(turn=8, friendly=_board(1, minions=3), opponent=_board(4, minions=2))
        await pipe.handle([ev.CombatStart(snapshot=snap)], historical=True)
        return sim.calls

    assert asyncio.run(run()) == []


def test_a_live_combat_still_gets_odds():
    async def run():
        pipe, sim, _ = _pipeline()
        snap = BoardSnapshot(turn=8, friendly=_board(1, minions=3), opponent=_board(4, minions=2))
        await pipe.handle([ev.CombatStart(snapshot=snap)])
        return sim.calls

    assert asyncio.run(run()) == [None], "one live simulation, at the default trial count"


def test_catch_up_still_remembers_the_boards_it_saw():
    """Skipping the simulation must not skip opponent memory — the scout popout
    and the shop forecast are both built from boards seen in past combats."""
    async def run():
        pipe, sim, _ = _pipeline()
        snap = BoardSnapshot(turn=8, friendly=_board(1, minions=3), opponent=_board(7, minions=2))
        await pipe.handle([ev.CombatStart(snapshot=snap)], historical=True)
        return pipe.memory.last_seen(7)

    assert asyncio.run(run()) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_shop_forecast.py -k "catch_up or live_combat" -v`
Expected: FAIL — `TypeError: handle() got an unexpected keyword argument 'historical'`.

- [ ] **Step 3: Thread the flag through the pipeline**

In `bgtracker/app.py`, change the signature and the one call site:

```python
    async def handle(self, events: list[ev.Event], historical: bool = False) -> None:
        """Fan events out. `historical` marks a batch the tracker did not watch
        happen — the drain of a session log that already existed when it
        started — so it rebuilds state and memory without paying for odds
        nobody is waiting on.
        """
```

and inside the `match`:

```python
                case ev.CombatStart(snapshot=snap):
                    # The real fight supersedes any guess about it, and must
                    # never queue behind one.
                    self._cancel_shop_forecast()
                    self.memory.record(snap.turn, snap.opponent)
                    prediction = await self._simulate(snap, historical)
                    self._pending = (snap, prediction)
```

and in `_simulate`:

```python
    async def _simulate(self, snapshot: BoardSnapshot, historical: bool = False) -> SimResult | None:
        # A combat that already happened cannot be forecast, and the player is
        # not waiting on it. Ahead of the board check so a catch-up prints
        # nothing at all.
        if self.sim is None or historical:
            return None
```

The shop forecast needs no guard: `handle` never awaits anything during a historical batch, so the debounced task cannot run until the batch is finished — at which point one forecast fires against the *current* board, which is correct.

- [ ] **Step 4: Mark the first read of each session in the tail loop**

In `bgtracker/__main__.py`, `live()`, add beside the other loop state:

```python
    idle_streak = 0
    # The first read of a session drains whatever is already in the file, which
    # is by definition history the tracker did not watch happen. Every read
    # after it is live — including the case where Power.log did not exist yet,
    # since a game that starts while we are watching is not catch-up.
    catching_up = True
```

In the new-session block, beside `idle_streak = 0`:

```python
                    idle_streak = 0   # a new session is activity by definition
                    catching_up = True
```

and at the read:

```python
            if tailer and processor:
                lines = tailer.read_new_lines()
                idle_streak = 0 if lines else idle_streak + 1
                status.note_lines(len(lines))
                await pipeline.handle(processor.feed(lines), historical=catching_up)
                catching_up = False
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_shop_forecast.py -v`
Expected: PASS.

- [ ] **Step 6: Verify against a real log**

```bash
ls -t ~/.local/share/Steam/steamapps/compatdata/*/pfx/drive_c/Hearthstone/Logs/Hearthstone_*/Power.log 2>/dev/null | head -1
# or use any captured fixture:
time .venv/bin/python -m bgtracker --replay tests/fixtures/*/Power.log --odds 2>&1 | tail -5
```
`--replay` deliberately still simulates (it is not the live catch-up path), so this checks nothing regressed there. The live behaviour is what the unit tests cover.

- [ ] **Step 7: Run the whole suite and commit**

```bash
.venv/bin/python -m pytest tests/
git add bgtracker/app.py bgtracker/__main__.py tests/test_shop_forecast.py
git commit -m "$(cat <<'EOF'
Stop re-simulating combats that already happened

The tailer reads each session log from the top, so a restart mid-session
replays the whole thing — and Pipeline.handle matched CombatStart with no
notion of live versus historical, so every past combat in the log cost a full
8000-trial run, serialized, while the player is in a game. CLAUDE.md's "~3s
for an 86MB log" is parse time and never included this.

"The first read of a session" is exactly the right definition of catch-up,
whatever its size: a fresh Tailer reads from offset 0, so everything already
in the file is history the tracker did not watch happen, and everything after
is live. That includes the case where Power.log does not exist yet — a game
starting while we watch is not catch-up, and the flag clears on the empty read.

Opponent memory is still recorded, because the scout popout and the shop
forecast are both built from boards seen in past combats. What is skipped is
only the odds nobody is waiting on.

Known cost: restarting into a live combat loses that one fight's odds, and the
display recovers at the next ShopReady.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: The shop forecast gets its own lane

**Files:**
- Create: `sidecar/lanes.mjs`
- Modify: `sidecar/server.mjs` (`simulate`, the dispatch, one import)
- Modify: `bgtracker/sim/client.py` (`simulate`, `__init__`, `apply_config`)
- Modify: `bgtracker/app.py` (`_shop_forecast`)
- Modify: `bgtracker/config.py`, `bgtracker/settings.py`, `config.example.toml`
- Test: `tests/test_shop_forecast.py`, `tests/test_sim_lanes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SimClient.simulate(battle_info, on_partial=None, sims=None, background=False)` and a `SimClient.shop_workers: int` attribute. The sidecar payload gains `"background": bool` and `"reserve": int`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shop_forecast.py`, and **update the existing `StubSim`** — its signature is the one thing in the suite that a `simulate()` change breaks:

```python
class StubSim:
    """Records every simulate() call instead of running one."""

    def __init__(self):
        self.calls: list[int] = []
        self.lanes: list[bool] = []

    async def simulate(self, battle_info, on_partial=None, sims=None, background=False):
        self.calls.append(sims)
        self.lanes.append(background)
        return SimResult(
            won_percent=55, tied_percent=5, lost_percent=40,
            avg_damage_won=10, avg_damage_lost=8, sims_run=sims or 8000,
        )
```

and add:

```python
def test_the_shop_forecast_runs_in_the_background_lane():
    """A guide the player is shuffling minions against has no business taking
    the whole worker pool the real fight needs."""
    async def run():
        pipe, sim, _ = _pipeline()
        await pipe.handle([ev.TurnChange(turn=8), ev.NextOpponent(player_id=4)])
        await pipe.handle([ev.ShopBoard(board=_board(1, minions=2), turn=8)])
        await _drain(pipe)
        return sim.lanes

    assert asyncio.run(run()) == [True]


def test_the_combat_forecast_runs_in_the_foreground_lane():
    async def run():
        pipe, sim, _ = _pipeline()
        snap = BoardSnapshot(turn=8, friendly=_board(1, minions=3), opponent=_board(4, minions=2))
        await pipe.handle([ev.CombatStart(snapshot=snap)])
        return sim.lanes

    assert asyncio.run(run()) == [False]
```

Create `tests/test_sim_lanes.py`:

```python
"""Foreground and background jobs must not share a worker.

SimClient's request lock holds one job in flight at a time — except on
cancellation, where the shop forecast keeps running in the sidecar while the
combat forecast it yielded to starts. server.mjs's watchdog assumes that never
happens: retiring a stuck worker terminates it, and the `retired` flag then
suppresses the exit handler, so a job sharing that worker would lose shards
silently. Splitting the pool makes the assumption true by construction.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

LANES = Path(__file__).resolve().parent.parent / "sidecar" / "lanes.mjs"


def _lanes(pool_size, reserve):
    """The sidecar's own lane arithmetic, against a stand-in pool.

    Imported from lanes.mjs rather than reimplemented here — a copy of the rule
    in the test would pass no matter what the sidecar actually did.
    """
    code = (
        f"import {{ lanes }} from {str(LANES)!r};"
        f"const pool = Array.from({{length: {pool_size}}}, (_, i) => i);"
        f"console.log(JSON.stringify(lanes(pool, {reserve})));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", code],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_one_reserved_worker_splits_a_pool_of_four():
    assert _lanes(4, 1) == {"fg": [0, 1, 2], "bg": [3]}


def test_the_lanes_never_overlap():
    for pool in (2, 3, 4, 8):
        for reserve in range(1, pool + 2):
            got = _lanes(pool, reserve)
            assert not (set(got["fg"]) & set(got["bg"])), (pool, reserve, got)


def test_no_reservation_leaves_both_lanes_the_whole_pool():
    """reserve=0 is the pre-split behaviour, kept for bisecting."""
    assert _lanes(4, 0) == {"fg": [0, 1, 2, 3], "bg": [0, 1, 2, 3]}


def test_a_single_worker_cannot_reserve_and_the_foreground_keeps_it():
    assert _lanes(1, 1) == {"fg": [0], "bg": [0]}


def test_reserving_everything_still_leaves_the_foreground_a_worker():
    assert _lanes(4, 9)["fg"] == [0]


def test_a_background_job_pools_the_same_odds_as_a_foreground_one():
    """The lane must change which workers run the trials and nothing else.

    Sharding across one worker instead of three is the case where a pooling
    bug hides: percentages that were averaged rather than derived from summed
    counts look right on an even split and wrong on a lopsided one.
    """
    from bgtracker.sim.client import SimClient
    from bgtracker.sim.mapper import to_battle_info

    from .test_mapper import snapshot_from_synthetic

    async def run():
        sim = SimClient(workers=4, shop_workers=1, cpu_policy="off")
        try:
            assert await sim.ping()
            info = to_battle_info(snapshot_from_synthetic())
            fore = await sim.simulate(info, sims=2000)
            back = await sim.simulate(info, sims=2000, background=True)
            return fore, back
        finally:
            await sim.close()

    fore, back = asyncio.run(run())
    # The synthetic 2/3 loses to a 1/7 taunt every time, in either lane.
    assert fore.lost_percent == 100
    assert back.lost_percent == 100
    assert back.sims_run == 2000, "a one-worker lane must still run every trial"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_shop_forecast.py tests/test_sim_lanes.py -v`
Expected: FAIL. The lane tests cannot resolve `sidecar/lanes.mjs`; the shop-forecast tests fail because `background` is never passed.

- [ ] **Step 3: Split the pool in the sidecar**

Create `sidecar/lanes.mjs`:

```js
// Which workers a job may use.
//
// Foreground (the combat forecast) takes them from the START of the pool,
// background (the shop forecast) from the END, so the two never share one.
//
// That matters because SimClient's request lock holds one job in flight at a
// time *except* on cancellation: a shop forecast that yields to CombatStart
// keeps running in the sidecar while the real fight dispatches. server.mjs's
// watchdog assumes exclusivity — retireWorker terminates a worker and its
// `retired` flag then suppresses the exit handler — so a shared worker would
// cost the other job its shards with no error anywhere. Splitting the pool
// makes the assumption true by construction rather than by the lock alone.
//
// reserve = 0 asks for no split and gives both lanes the whole pool, which is
// the behaviour this replaced. A pool of one cannot reserve: the foreground
// keeps it, because the real fight is the one somebody is waiting on.
//
// Its own module so the arithmetic can be tested without importing server.mjs,
// which would spawn the pool and pay ~350MB for a card DB per worker.
export function lanes(pool, reserve) {
    const n = pool.length;
    const r = Math.max(0, Math.min(reserve | 0, n - 1));
    return { fg: pool.slice(0, n - r), bg: r ? pool.slice(n - r) : pool.slice(0) };
}
```

In `sidecar/server.mjs`, add to the imports at the top:

```js
import { lanes } from './lanes.mjs';
```

Change `simulate` to take and use the lane:

```js
async function simulate(id, input, sims, workers, deadline, background, reserve) {
    await poolReady();
    const total = sims ?? 8000;
    const split = lanes(pool, reserve);
    const lane = background ? split.bg : split.fg;
    const usable = Math.max(1, Math.min(lane.length, workers || lane.length));
    const shards = [];
    for (let i = 0; i < usable; i++) {
        // Spread the remainder so the shards differ by at most one trial.
        shards.push(Math.floor(total / usable) + (i < total % usable ? 1 : 0));
    }
    const jobId = ++nextJobId;
    const job = {
        id, expected: usable, shards: {}, done: new Set(), lastPartial: 0,
        // The worker entry each shard went to, so the watchdog can kill exactly
        // the ones that never report back.
        workers: lane.slice(0, usable), timer: null,
    };
    inflight.set(jobId, job);
    if (deadline) job.timer = setTimeout(() => onJobTimeout(jobId), deadline);
    shards.forEach((count, i) => {
        lane[i].worker.postMessage({ jobId, shard: i, input, sims: count });
    });
}
```

and the dispatch at the bottom:

```js
        else if (msg.op === 'simulate') {
            simulate(msg.id, msg.input, msg.sims, msg.workers, msg.deadline,
                     msg.background, msg.reserve)
                .catch((e) => out({ id: msg.id, error: String(e?.stack ?? e) }));
        }
```

- [ ] **Step 4: Add the config option**

In `bgtracker/config.py`, after `sim_cpu_policy`:

```python
    # Workers set aside for the recruit-phase shop forecast, so a guide the
    # player is shuffling minions against cannot take the pool the real fight
    # needs. 0 = no reservation (both share the whole pool).
    sim_shop_workers: int = 1
```

In `bgtracker/settings.py`, after the `sim_cpu_policy` entry:

```python
    Setting(
        "sim_shop_workers", "simulator", "int", 1,
        "Workers reserved for shop odds",
        "The recruit-phase forecast is a guide against a board of stated age, "
        "re-run every time you buy, sell or reposition. Giving it its own "
        "worker keeps it from taking the whole pool the real combat forecast "
        "needs, at the cost of it firming up more slowly. 0 shares the pool, "
        "which is the older behaviour.",
        channel=SIM, minimum=0, maximum=4, step=1,
    ),
```

Regenerate the example file:

```bash
.venv/bin/python -c "from bgtracker.settings import example_toml; open('config.example.toml','w').write(example_toml())"
```

- [ ] **Step 5: Send the lane from the client**

In `bgtracker/sim/client.py`, add `shop_workers` to `__init__` (after `cpu_policy`):

```python
        self.cpu_policy = cpu_policy
        # Workers the sidecar sets aside for background jobs. Read fresh per
        # request, so it needs no respawn.
        self.shop_workers = shop_workers
```

with the parameter `shop_workers: int = 1` added to the signature.

Change `simulate`:

```python
    async def simulate(
        self,
        battle_info: dict,
        on_partial=None,
        sims: int | None = None,
        background: bool = False,
    ) -> SimResult:
        """Run a combat.

        `on_partial`, if given, is called with a provisional SimResult each time
        the run tightens — a usable number lands in a fraction of the time the
        full run takes, which matters on the boards that take seconds.

        `background` puts the job in the sidecar's reserved lane, at the end of
        the worker pool. The shop forecast uses it: it is a guide, re-run
        constantly, and it must not take the workers the real fight needs or
        share one with it.
        """
```

and add the two fields to the request payload, beside `sims`:

```python
                "sims": sims or self.sims,
                "background": background,
                "reserve": self.shop_workers,
```

In `apply_config`:

```python
    def apply_config(self, cfg) -> None:
        """Adopt trial count, timeout and lane reservation. All are read fresh
        per `simulate()`, so assignment is the entire mechanism — no restart,
        nothing in flight disturbed."""
        self.sims = cfg.sim_count
        self.timeout = cfg.sim_timeout
        self.shop_workers = cfg.sim_shop_workers
```

In `bgtracker/__main__.py`, `start_sim`, pass it at construction:

```python
    sim = SimClient(
        timeout=cfg.sim_timeout, sims=cfg.sim_count, workers=cfg.sim_workers,
        cpu_policy=cfg.sim_cpu_policy, shop_workers=cfg.sim_shop_workers,
    )
```

- [ ] **Step 6: Use the lane from the pipeline**

In `bgtracker/app.py`, `_shop_forecast`:

```python
            result = await self.sim.simulate(
                info, sims=SHOP_SIM_COUNT, background=True
            )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_shop_forecast.py tests/test_sim_lanes.py tests/test_settings_schema.py -v`
Expected: PASS.

- [ ] **Step 8: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/`
Expected: PASS. `test_sim_roundtrip.py` and `test_sim_watchdog.py` exercise the real sidecar through the changed dispatch — a failure there means the lane arithmetic broke normal jobs.

- [ ] **Step 9: Commit**

```bash
git add sidecar/lanes.mjs sidecar/server.mjs bgtracker/sim/client.py bgtracker/app.py \
        bgtracker/config.py bgtracker/settings.py config.example.toml \
        tests/test_shop_forecast.py tests/test_sim_lanes.py
git commit -m "$(cat <<'EOF'
Give the shop forecast its own worker instead of the whole pool

server.mjs already accepted a per-job worker count and SimClient never sent
one, so a 2000-trial guide got the same four-core fan-out as the fight of
record — fired again on every buy, sell and reposition, which is the recruit
phase the author sees hitches in.

Splitting the pool rather than just asking for fewer workers is what makes the
watchdog's assumption true. SimClient's lock holds one job in flight except on
cancellation: a shop forecast that yields to CombatStart keeps running in the
sidecar while the real fight dispatches, and a longer shop job widens that
window several times over. retireWorker terminates a worker and its `retired`
flag then suppresses the exit handler, so a shared worker would cost the other
job its shards with no error anywhere. Foreground takes the head of the pool,
background the tail; reserve=0 restores the old shared behaviour for bisecting.

Costs the combat forecast a third of its throughput on the default four
workers, which is why sim_workers gets re-measured rather than assumed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Checkpoint — measure B, set the defaults, update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`
- Modify: `bgtracker/config.py`, `bgtracker/settings.py`, `config.example.toml` (only if the bench says `sim_workers` should move)

- [ ] **Step 1: Capture frametimes with A and B both in**

Same procedure as Task 2. Compare against the Task 2 baselines and the Task 7 numbers.

- [ ] **Step 2: Bench the worker count**

The combat forecast is now on 3 workers on efficiency cores, where the default of 4 was chosen on P-cores at normal priority. Measure rather than assume:

```bash
for n in 4 6 8; do
  echo "== sim_workers=$n"
  BGTRACKER_SIM_WORKERS=$n .venv/bin/python -m bgtracker resim 40 | grep -E "wall time|under 4000|mean drift"
done
```

Each worker costs ~350MB, so 8 is ~2.8GB against 62GB — affordable if it buys throughput.

- [ ] **Step 3: Set `sim_workers` from the numbers**

If a higher count materially lowers the wall-time mean **and** removes any truncation, change the default in `bgtracker/config.py` and `bgtracker/settings.py` (both, or `test_settings_schema.py` fails) and regenerate `config.example.toml`. If it does not, leave it at 0 and say so in the commit — a measurement that changed nothing is still the answer.

- [ ] **Step 4: Confirm the odds did not move**

```bash
.venv/bin/python -m bgtracker resim 60
```
Neither A nor B is supposed to change a prediction. Judge on **Brier direction, not mean drift**, and treat any `sims_run` under 8000 as the real finding — 589 recorded combats were a flat 8000 before this work.

- [ ] **Step 5: Update CLAUDE.md**

The working tree already has unrelated uncommitted CLAUDE.md edits — **rebase your additions on top of them, do not revert them**. Add:

- to the **Layer boundaries** table: `| Simulator CPU policy (efficiency cores, idle priority) | `sim/cpu.py` |`
- to the **Settings** channel discussion: nothing new (both options reuse `SIM` and `SIM_RESPAWN`).
- to **Odds accuracy workflow**, beside the existing resim gotchas: that `resim` now prints wall time and doubles as the throughput bench.
- a new bullet under **Log and tag facts that are easy to get wrong** — or better, under the history section — recording that `record_combat` and `end_game` are COALESCE writes because catch-up re-records, and that `ended_at` in rows predating this is wrong.
- to the **Before calling anything done** table: `| the sidecar spawn, `sim/cpu.py`, or lane splitting | a frametime capture, judged on 0.1% low |`

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/superpowers/plans/2026-07-28-sim-cpu-isolation.md
# plus config.py settings.py config.example.toml if the default moved
git commit -m "$(cat <<'EOF'
Record the post-change measurements and document the new invariants

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Merge**

```bash
.venv/bin/python -m pytest tests/
git checkout master
git merge --no-ff sim-cpu-isolation -m "$(cat <<'EOF'
Keep the combat simulator off the cores the game is drawing on

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Measurements

### Frametimes — NOT CAPTURED

| When | 0.1% low | 1% low | median | max | Notes |
|---|---|---|---|---|---|
| baseline, tracker stopped | — | — | — | — | control, not captured |
| baseline, tracker running | — | — | — | — | not captured |
| after A | — | — | — | — | not captured |
| after A + B | — | — | — | — | not captured |

**Outstanding.** MangoHud capture needs a play session set up in advance
(`MANGOHUD=1 MANGOHUD_CONFIG=output_folder=/tmp/ft,toggle_logging=F2`), and the branch
merged before one happened. **The hitch diagnosis therefore remains inference, not
measurement**: the two moments the author reports stuttering are the two moments the
simulator runs, and the recorded 387ms mean / 1859ms max of unniced four-thread work at a
combat transition is a plausible cause — but no preempted frame was ever observed. If a
capture shows the hitches survive the change, the mechanism is not what this branch
assumed and the design should be reconsidered rather than extended.

### Accuracy — measured, and the change is clean

`resim 40` after change A: **mean drift 0.14 points, 0 rows moved ≥1 point, no
truncation warning.** Change A cost no accuracy. Every one of the 589 combats recorded
before this branch has `sims_run = 8000`, so any future row below 8000 is the first sign
that a scheduling default has gone too far — that is the standing tripwire.

### Throughput — not validly measured

| Config | resim wall mean | wall max | runs under 4000 trials |
|---|---|---|---|
| before (P-cores, 4 workers) | 387 (from `history.db` `sim_ms`) | 1859 | 0 of 589 recorded |
| after A (E-cores) | *(2170)* | *(8775)* | 0 of 40 |
| 6 / 8 workers | — | — | not run |

The parenthesised numbers are **discarded, not results.** They were taken while a live
game was running *and* the author's own tracker was simulating real combats on the same
16 efficiency cores at the same idle priority — two `SCHED_IDLE` sidecars sharing a lane
plus the game itself. They are not a measurement of this change.

### `sim_workers` — left at its default, deliberately unmeasured

The plan's open decision is **unresolved**. The default stays `0` (the sidecar's own 4).
The combat forecast now runs on 3 workers instead of 4, on 4.4 GHz cores instead of
5.7–6.0, and whether raising the count recovers that was never benched. `resim` can now
answer it — `SimClient.from_config` means `sim_cpu_policy` and `sim_workers` reach it, and
`BGTRACKER_SIM_WORKERS=<n> bgtracker resim 40` sweeps the count — but it must be run on a
quiet machine. Raising the default without that measurement would be exactly the
argument-instead-of-evidence the spec refused.
