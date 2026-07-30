"""Finding the cores the game does not want.

Hybrid Intel parts pair fast P-cores with slower E-cores. The kernel sometimes
says which is which outright and sometimes does not — the author's does not —
so the frequency fallback is load-bearing rather than theoretical.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from bgtracker.sim.cpu import efficiency_cpus, spawn_preexec


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
    monkeypatch.setattr(os, "setpriority", lambda which, who, prio: seen.update(priority=(which, who, prio)))

    spawn_preexec("auto", tmp_path)()

    assert seen["affinity"] == (0, {2, 3})
    assert seen["policy"] == (0, os.SCHED_IDLE)
    assert seen["priority"] == (os.PRIO_PROCESS, 0, 19)


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
    monkeypatch.setattr(os, "setpriority", boom)

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
    monkeypatch.setattr(os, "setpriority", lambda which, who, prio: seen.update(priority=prio))

    spawn_preexec("auto", tmp_path)()

    assert "affinity" not in seen, "nothing to pin to on a uniform CPU"
    assert seen["policy"] == os.SCHED_IDLE
    assert seen["priority"] == 19


def test_it_really_works_on_this_kernel():
    """Out of process, because applying it in the test runner would leave
    pytest itself at SCHED_IDLE and nice 19 for the rest of the suite.

    The monkeypatched tests above prove the guard logic; this proves the
    syscalls are actually accepted by the kernel we run on.
    """
    repo_root = Path(__file__).resolve().parent.parent
    code = (
        "import os, sys;"
        "sys.path.insert(0, %r);"
        "from bgtracker.sim.cpu import spawn_preexec, efficiency_cpus;"
        "spawn_preexec('auto')();"
        "print(os.sched_getscheduler(0), len(os.sched_getaffinity(0)),"
        " len(efficiency_cpus() or []))"
    ) % str(repo_root)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    policy, affinity, efficiency = (int(n) for n in out.stdout.split())
    assert policy == os.SCHED_IDLE
    if efficiency:
        assert affinity == efficiency, "affinity should be exactly the efficiency cores"
