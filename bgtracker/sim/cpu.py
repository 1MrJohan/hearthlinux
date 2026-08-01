"""Which CPUs the simulator is allowed to run on.

Hybrid Intel parts (Alder Lake onward) pair fast P-cores with slower E-cores.
The game wants the P-cores and cannot be asked to share them; the simulator
does not care which cores it gets so long as it gets some. Finding the E-cores
is therefore the whole job of this module.
"""

from __future__ import annotations

import ctypes
import logging
import os
import signal
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


# The nice fallback for a kernel that refuses sched_setscheduler; SCHED_IDLE
# ignores nice entirely, so on a working kernel this does nothing.
_NICE = 19

# prctl(2) constant; asks the kernel to deliver this signal to the child when
# its parent thread dies. Not in the os module on any Python we support.
_PR_SET_PDEATHSIG = 1


def spawn_preexec(policy: str = "auto", root: Path = SYSFS_CPU) -> Callable[[], None]:
    """A `preexec_fn` that makes the sidecar defer to whatever else is running
    — and die with the tracker rather than outliving it.

    Always returns an applier, whatever the policy: the parent-death signal is
    not a scheduling opinion. Both routine shutdown paths are SIGTERM
    (`steam-launch.sh` when Hearthstone exits, `--replace` killing the old
    instance), which terminates Python without unwinding, so the
    `finally: await sim.close()` never runs and a node parent plus its ~350MB
    workers used to survive every restart. PR_SET_PDEATHSIG makes the kernel
    deliver SIGTERM to the sidecar when the tracker dies, however it dies.
    The known gap: the flag is cleared if this child ever forks-and-parents
    its own daemon (node does not), and a parent that dies in the microseconds
    between fork and prctl leaves one orphan — a race, not a leak pattern.

    **Why before exec rather than on the running process.** Affinity and
    scheduling policy are per-*thread* on Linux and inherited at thread
    creation, not applied retroactively to a process. The sidecar's workers are
    created at module load, before the parent has even read the `ready` line,
    and `retireWorker` spawns more of them mid-game when the watchdog kills a
    hung one. Setting the mask here is the only point that covers every thread,
    present and future, with no race and no walk of /proc/<pid>/task.

    Not literally hazard-free: `preexec_fn`'s documented risk is threads in the
    parent, and this process does have some — asyncio's Unix child-watcher
    machinery and GLib's worker thread are both runtime-owned, not code this
    file wrote, and `os.sched_setaffinity` does allocate a `cpu_set_t` rather
    than being a bare syscall. That is an accepted risk, not a solved one:
    glibc's `pthread_atfork` handlers reinitialise malloc arenas in the child
    and the exposed window is microseconds, which is why `preexec_fn` is still
    the right mechanism here despite it. What keeps the risk this low is that
    `apply()` stays three thin syscall wrappers guarded by their own
    `try`/`except` — CLAUDE.md's no-threads rule keeps *our* code out of the
    fork hazard, but it says nothing about what a future edit puts in this
    function. Do not read "safe" as licence to make `apply()` heavier.

    **Why SCHED_IDLE and not merely nice.** The simulator already degrades
    gracefully under starvation: `maxAcceptableDuration` truncates the run and
    the sidecar pools whatever shards finished, so scheduler pressure costs
    sample size — a wider `margin`, recorded in `sims_run` — never correctness.
    That is what licenses the aggressive setting.
    """
    scheduling = policy == "auto"
    cpus = efficiency_cpus(root) if scheduling else None
    if scheduling:
        if cpus:
            log.info("simulator pinned to %d efficiency cores at idle priority", len(cpus))
        else:
            log.info("simulator at idle priority (no efficiency cores to pin to)")
    # The CDLL load happens here in the parent — loading a shared object is
    # exactly the kind of allocation-heavy work the fork hazard forbids in
    # apply(); only the bare prctl call crosses the fork.
    try:
        _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        _libc = None

    def apply() -> None:
        # Nothing here may raise: an exception inside a preexec_fn surfaces in
        # the parent as a SubprocessError and would take the sidecar down.
        # Failing to lower our own priority is not a reason to have no odds.
        if _libc is not None:
            try:
                _libc.prctl(_PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0)
            except Exception:
                pass
        if not scheduling:
            return
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
            # setpriority takes an absolute value; os.nice(19) is a relative
            # increment applied on top of whatever nice level the launching
            # shell already sits at (measured 15, not 19, on the author's
            # machine, whose shell starts at -4). Inert while SCHED_IDLE
            # holds — SCHED_IDLE ignores nice entirely — but that is exactly
            # the kernel where getting the fallback right matters.
            os.setpriority(os.PRIO_PROCESS, 0, _NICE)
        except OSError:
            pass

    return apply
