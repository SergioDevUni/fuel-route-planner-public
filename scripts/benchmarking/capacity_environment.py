"""Record actual process affinity and container resource ceilings."""

import ctypes
import os
from pathlib import Path


def _windows_affinity():
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = ctypes.c_void_p
    allowed, system = ctypes.c_size_t(), ctypes.c_size_t()
    query = ctypes.windll.kernel32.GetProcessAffinityMask
    query.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_size_t),
    )
    if not query(process(), ctypes.byref(allowed), ctypes.byref(system)):
        raise OSError("cannot read process affinity")
    return process(), allowed.value


def restrict_cpu_affinity(count):
    """Limit only this benchmark process; inherited HTTP/planner threads follow."""
    if count < 1:
        raise ValueError("CPU affinity count must be positive")
    if os.name == "nt":
        process, mask = _windows_affinity()
        available = [i for i in range(mask.bit_length()) if mask & (1 << i)]
        if count > len(available):
            raise ValueError("CPU affinity exceeds available processors")
        setter = ctypes.windll.kernel32.SetProcessAffinityMask
        setter.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
        if not setter(process, sum(1 << i for i in available[:count])):
            raise OSError("cannot set process affinity")
    elif hasattr(os, "sched_setaffinity"):
        available = sorted(os.sched_getaffinity(0))
        if count > len(available):
            raise ValueError("CPU affinity exceeds available processors")
        os.sched_setaffinity(0, available[:count])
    else:
        raise ValueError("CPU affinity is unsupported on this platform")


def resource_environment():
    if os.name == "nt":
        _, mask = _windows_affinity()
        affinity = [i for i in range(mask.bit_length()) if mask & (1 << i)]
    else:
        affinity = (
            sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else None
        )
    cgroup = {}
    for name in ("cpu.max", "cpu.stat", "io.stat", "memory.max", "memory.peak"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            cgroup[name] = path.read_text().strip()
    return {
        "host_logical_cpus": os.cpu_count(),
        "process_affinity": affinity,
        "cgroup_v2": cgroup,
        "memory_limit_measured": "memory.max" in cgroup,
    }
