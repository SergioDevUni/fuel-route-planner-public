"""Route-independent final-verification reserves from recent observations."""

from collections import defaultdict, deque
from threading import Lock


class VerificationTiming:
    def __init__(self) -> None:
        self._lock = Lock()
        self._samples: dict[tuple[str, int], deque[float]] = defaultdict(
            lambda: deque(maxlen=64)
        )

    def observe(self, profile: str, waypoints: int, seconds: float) -> None:
        with self._lock:
            self._samples[profile, waypoints.bit_length()].append(seconds)

    def reserve(self, profile: str, waypoints: int) -> float:
        with self._lock:
            samples = sorted(self._samples[profile, waypoints.bit_length()])
        if len(samples) < 8:
            return 4.0
        tail = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
        return min(6.5, max(3.0, tail + 0.75))


verification_timing = VerificationTiming()
