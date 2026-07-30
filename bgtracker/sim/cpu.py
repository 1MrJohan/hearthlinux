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
