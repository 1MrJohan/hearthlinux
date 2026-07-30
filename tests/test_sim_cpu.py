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
