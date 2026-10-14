"""Process RSS and sampled runtime pressure for offline capacity probes."""

import ctypes
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

from app.api.deadline import planning_activity


def resource_counters():
    """Cumulative cgroup counters; subtract them per measurement window."""
    counters = {}
    cpu = Path("/sys/fs/cgroup/cpu.stat")
    io = Path("/sys/fs/cgroup/io.stat")
    if cpu.is_file():
        counters.update(
            {
                "cpu_" + k: int(v)
                for k, v in (line.split() for line in cpu.read_text().splitlines())
            }
        )
    if io.is_file():
        for line in io.read_text().splitlines():
            for item in line.split()[1:]:
                name, value = item.split("=")
                counters["io_" + name] = counters.get("io_" + name, 0) + int(value)
    return counters


def resident_bytes():
    if os.name == "nt":

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                *(
                    (name, ctypes.c_size_t)
                    for name in (
                        "PeakWorkingSetSize",
                        "WorkingSetSize",
                        "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage",
                        "PagefileUsage",
                        "PeakPagefileUsage",
                    )
                ),
            ]

        data = Counters()
        data.cb = ctypes.sizeof(data)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        if query(process(), ctypes.byref(data), data.cb):
            return data.WorkingSetSize
    elif os.path.exists("/proc/self/statm"):
        with open("/proc/self/statm") as stream:
            return int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return None


class PressureSample:
    def __init__(self):
        self.stop = threading.Event()
        self.peak = {"active_planners": 0, "late_planners": 0, "waiting_planners": 0}
        self.rss = resident_bytes()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop.wait(0.02):
            activity = planning_activity()
            for key in self.peak:
                self.peak[key] = max(self.peak[key], activity[key])
            value = resident_bytes()
            if value is not None:
                self.rss = max(self.rss or 0, value)

    def __enter__(self):
        self.resources_before = resource_counters()
        self.cpu = time.process_time()
        self.wall = time.perf_counter()
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=1)
        self.cpu_seconds = time.process_time() - self.cpu
        self.wall_seconds = time.perf_counter() - self.wall
        self.resource_deltas = {
            key: value - self.resources_before.get(key, value)
            for key, value in resource_counters().items()
        }

    def report(self):
        return {
            **self.peak,
            "peak_rss_bytes": self.rss,
            "process_cpu_seconds": round(self.cpu_seconds, 4),
            "process_cpu_per_wall_second": round(
                self.cpu_seconds / self.wall_seconds, 3
            ),
            "wall_seconds": round(self.wall_seconds, 4),
            "cgroup_counter_deltas": self.resource_deltas,
        }
