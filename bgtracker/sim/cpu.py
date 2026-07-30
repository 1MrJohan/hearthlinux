"""Which CPUs the simulator is allowed to run on.

Hybrid Intel parts (Alder Lake onward) pair fast P-cores with slower E-cores.
The game wants the P-cores and cannot be asked to share them; the simulator
does not care which cores it gets so long as it gets some. Finding the E-cores
is therefore the whole job of this module.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
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
