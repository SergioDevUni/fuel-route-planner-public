"""Versioned immutable road facts for baselines and repeated stop sequences."""

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol, cast

from app.domain.routing import Coordinates, Route
from app.planning.budget import RequestBudget

logger = logging.getLogger(__name__)


class RouteProvider(Protocol):
    def route(
        self, waypoints: tuple[Coordinates, ...], budget: RequestBudget
    ) -> Route: ...


class AlternativeRouteProvider(Protocol):
    def shorter_route(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> Route: ...


class BaselineRouteCache:
    """Reuse road facts; callers still check each trip's fuel and route policy."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 6 * 60 * 60,
        max_entries: int = 128,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries < 1:
            raise ValueError("baseline cache limits must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._guard = threading.Lock()
        self._entries: dict[
            tuple[str, tuple[Coordinates, ...]], tuple[float, Route]
        ] = {}

    def get(
        self,
        version: str,
        points: tuple[Coordinates, ...],
        budget: RequestBudget,
    ) -> Route | None:
        budget.check_deadline()
        with self._guard:
            entry = self._entries.get((version, points))
            if entry is None or entry[0] <= self.clock():
                return None
            if len(points) == 2:
                logger.info("baseline_route_cache_hit")
            else:
                budget.increment_metric("selected_route_cache_hits", 1)
            return entry[1]

    def put(
        self,
        version: str,
        points: tuple[Coordinates, ...],
        route: Route,
    ) -> None:
        with self._guard:
            ttl = self.ttl_seconds if len(points) == 2 else min(self.ttl_seconds, 600)
            self._entries[(version, points)] = (self.clock() + ttl, route)
            if len(self._entries) > self.max_entries:
                oldest = min(self._entries, key=lambda key: self._entries[key][0])
                del self._entries[oldest]


baseline_route_cache = BaselineRouteCache()
local_baseline_route_cache = BaselineRouteCache(max_entries=8)


class CachedBaselineRouter:
    """Optionally reuse exact selected-stop road facts under the same profile."""

    def __init__(
        self,
        router: RouteProvider,
        cache: BaselineRouteCache,
        version: str,
        *,
        cache_selected_routes: bool = False,
    ) -> None:
        if not version:
            raise ValueError("baseline cache version is required")
        self.router = router
        self.cache = cache
        self.version = version
        self.cache_selected_routes = cache_selected_routes

    def route(self, waypoints: tuple[Coordinates, ...], budget: RequestBudget) -> Route:
        if len(waypoints) != 2 and not self.cache_selected_routes:
            return self.router.route(waypoints, budget)
        cached = self.cache.get(self.version, waypoints, budget)
        if cached is not None:
            return cached
        route = self.router.route(waypoints, budget)
        self.cache.put(self.version, waypoints, route)
        return route

    @property
    def supports_shorter_routes(self) -> bool:
        return bool(getattr(self.router, "supports_shorter_routes", False))

    def shorter_route(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> Route:
        # Alternative geometry bypasses the fastest-baseline cache entirely.
        return cast(AlternativeRouteProvider, self.router).shorter_route(points, budget)
