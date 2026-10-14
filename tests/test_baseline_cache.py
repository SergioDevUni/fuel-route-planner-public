"""A cached baseline frees a hosted call for later verification repair."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.planning.baseline_cache import BaselineRouteCache, CachedBaselineRouter
from app.planning.budget import RequestBudget

START = Coordinates(40, -90)
FINISH = Coordinates(40, -89)
OTHER = Coordinates(40, -88)
ALTERNATE = Coordinates(40, -87)


class CountingRouter:
    def __init__(self) -> None:
        self.calls = 0

    def route(self, points, budget):
        budget.consume_attempt()
        self.calls += 1
        return Route(
            geometry=points,
            waypoints=tuple(
                RouteWaypoint(point, point, index, Decimal(0))
                for index, point in enumerate(points)
            ),
            legs=tuple(
                RouteLeg(Decimal(1000), Decimal(60)) for _ in range(len(points) - 1)
            ),
            distance_meters=Decimal(1000 * (len(points) - 1)),
            duration_seconds=Decimal(60 * (len(points) - 1)),
            engine_version="test",
            fetched_at=datetime.now(UTC),
        )


def test_same_endpoint_new_trip_reuses_baseline_and_frees_repair_attempt() -> None:
    now = [1000.0]
    provider = CountingRouter()
    cache = BaselineRouteCache(clock=lambda: now[0])
    router = CachedBaselineRouter(provider, cache, "road-v1")
    first = RequestBudget()
    original = router.route((START, FINISH), first)
    assert first.attempts == 1

    second = RequestBudget()
    assert router.route((START, FINISH), second) is original
    assert second.attempts == 0
    second.consume_attempt()  # fresh matrix in a real trip
    router.route((START, OTHER, FINISH), second)  # selected final directions
    router.route((START, ALTERNATE, FINISH), second)  # changed-waypoint repair
    assert second.attempts == 3
    assert provider.calls == 3


def test_baseline_cache_expires_and_is_keyed_by_endpoint_and_road_version() -> None:
    now = [1000.0]
    provider = CountingRouter()
    cache = BaselineRouteCache(ttl_seconds=100, clock=lambda: now[0])
    v1 = CachedBaselineRouter(provider, cache, "road-v1")
    v2 = CachedBaselineRouter(provider, cache, "road-v2")
    v1.route((START, FINISH), RequestBudget())
    v1.route((START, FINISH), RequestBudget())
    assert provider.calls == 1
    v1.route((START, OTHER), RequestBudget())
    v2.route((START, FINISH), RequestBudget())
    assert provider.calls == 3
    now[0] += 101
    v1.route((START, FINISH), RequestBudget())
    assert provider.calls == 4


def test_local_graph_baseline_cache_versions_and_final_routes() -> None:
    provider = CountingRouter()
    cache = BaselineRouteCache(max_entries=8)
    current = CachedBaselineRouter(provider, cache, "valhalla-auto:us-graph-v1")
    updated = CachedBaselineRouter(provider, cache, "valhalla-auto:us-graph-v2")

    current.route((START, FINISH), RequestBudget())
    current.route((START, FINISH), RequestBudget())
    assert provider.calls == 1
    current.route((START, OTHER, FINISH), RequestBudget())
    current.route((START, OTHER, FINISH), RequestBudget())
    assert provider.calls == 3
    updated.route((START, FINISH), RequestBudget())
    assert provider.calls == 4
