"""Thread-safe, bounded per-process caller quotas; no proxy header parsing."""

import math
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from time import monotonic


class CallerRateLimiter:
    def __init__(
        self,
        requests: int,
        window_seconds: int,
        max_callers: int = 10000,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if requests < 1 or window_seconds < 1 or max_callers < 1:
            raise ValueError("rate limiter bounds must be positive")
        self.requests = requests
        self.window_seconds = window_seconds
        self.max_callers = max_callers
        self.clock = clock
        self._callers: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._lock = Lock()

    def check(self, caller: str) -> int:
        """Consume one request, or return a positive Retry-After in seconds."""
        with self._lock:
            # Timestamp order must follow insertion order; sampling before the
            # lock allows thread preemption to reorder caller expirations.
            now = self.clock()
            while self._callers:
                _, (_, expires) = next(iter(self._callers.items()))
                if expires > now:
                    break
                self._callers.popitem(last=False)
            entry = self._callers.get(caller)
            if entry is None:
                if len(self._callers) >= self.max_callers:
                    expires = next(iter(self._callers.values()))[1]
                    return max(1, math.ceil(expires - now))
                entry = (0, now + self.window_seconds)
            count, expires = entry
            if count >= self.requests:
                return max(1, math.ceil(expires - now))
            self._callers[caller] = (count + 1, expires)
            return 0
