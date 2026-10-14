"""Small per-process completed-result cache with same-key coalescing."""

import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.planning.budget import RequestBudget

Payload = dict[str, Any]
Rank = tuple[Decimal, Decimal, int, tuple[str, ...]]


@dataclass
class _Flight:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


class ResultCache:
    def __init__(self, ttl_seconds: float = 300, max_entries: int = 128) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._guard = threading.Lock()
        self._locks: dict[str, _Flight] = {}
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def _cached(self, key: str) -> dict[str, Any] | None:
        with self._guard:
            cached = self._entries.get(key)
            payload = cached[1] if cached and cached[0] > time.monotonic() else None
        # Cache-owned snapshots are never mutated. Holding this reference keeps
        # it valid across replacement/eviction without blocking other readers.
        return deepcopy(payload) if payload is not None else None

    def get_completed(self, key: str) -> dict[str, Any] | None:
        """Read a deep-copied completed result before planning admission."""
        return self._cached(key)

    def put_completed(self, key: str, payload: dict[str, Any]) -> None:
        """Store verified planning output, before per-request persistence metadata."""
        snapshot = deepcopy(payload)
        with self._guard:
            self._entries[key] = (
                time.monotonic() + self.ttl_seconds,
                snapshot,
            )
            if len(self._entries) > self.max_entries:
                oldest = min(self._entries, key=lambda item: self._entries[item][0])
                del self._entries[oldest]

    def get_or_compute(
        self,
        key: str,
        compute: Callable[[], dict[str, Any]],
        budget: RequestBudget | None = None,
    ) -> dict[str, Any]:
        if budget is not None:
            budget.check_deadline()
        cached = self._cached(key)
        if cached is not None:
            return cached
        with self._guard:
            flight = self._locks.setdefault(key, _Flight())
            flight.users += 1
        acquired = False
        try:
            with budget.stage("result_cache_wait") if budget else nullcontext():
                if budget is None:
                    flight.lock.acquire()
                    acquired = True
                else:
                    while not flight.lock.acquire(
                        timeout=min(0.05, budget.remaining_seconds)
                    ):
                        budget.check_deadline()
                        # A preferred response can finish while its optional
                        # worker drains. Same-key waiters may use that verified
                        # result without waiting for the worker's flight lock.
                        cached = self._cached(key)
                        if cached is not None:
                            return cached
                    acquired = True
            if budget is not None:
                budget.check_deadline()
            cached = self._cached(key)
            if cached is not None:
                return cached
            result = compute()
            if budget is not None:
                budget.check_deadline()
            self.put_completed(key, result)
            return result
        finally:
            if acquired:
                flight.lock.release()
            with self._guard:
                flight.users -= 1
                if flight.users == 0:
                    del self._locks[key]


trip_cache = ResultCache()


class VerifiedIncumbentCache:
    """Feasibility survives expansion of road-source availability, within TTL."""

    def __init__(self, ttl_seconds: float = 300, max_entries: int = 64) -> None:
        self.ttl_seconds, self.max_entries = ttl_seconds, max_entries
        self._guard = threading.Lock()
        self._entries: dict[str, dict[str, tuple[float, Rank, Payload]]] = {}

    def get(self, key: str) -> dict[str, tuple[Rank, Payload]]:
        with self._guard:
            now = time.monotonic()
            return deepcopy(
                {
                    profile: (rank, payload)
                    for profile, (expiry, rank, payload) in self._entries.get(
                        key, {}
                    ).items()
                    if expiry > now
                }
            )

    def put(
        self,
        key: str,
        profile: str,
        rank: tuple[Decimal, Decimal, int, tuple[str, ...]],
        payload: dict[str, Any],
    ) -> None:
        with self._guard:
            now = time.monotonic()
            profiles = self._entries.setdefault(key, {})
            old = profiles.get(profile)
            if old is None or old[0] <= now or rank <= old[1]:
                profiles[profile] = (now + self.ttl_seconds, rank, deepcopy(payload))
            if len(self._entries) > self.max_entries:
                del self._entries[
                    min(
                        self._entries,
                        key=lambda k: max(v[0] for v in self._entries[k].values()),
                    )
                ]


verified_incumbent_cache = VerifiedIncumbentCache()
