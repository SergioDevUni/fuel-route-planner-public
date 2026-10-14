from dataclasses import replace
from decimal import Decimal

import pytest

from app.planning.baseline_cache import BaselineRouteCache, CachedBaselineRouter
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.route_verification import verify_selection
from app.planning.trip_context import PlanningContext
from app.planning.trip_types import DurationLimitExceeded, RouteCheckFailed
from tests.support.planning import FINISH, START, STATIONS, FakeCandidates, _route


class Roads:
    def __init__(self):
        self.calls = 0

    def route(self, points, budget):
        budget.consume_attempt()
        self.calls += 1
        return _route(points, (400, 400, 200))


def verify(context, selected, initial=Decimal(50), reserve=Decimal(0), limit=None):
    return verify_selection(
        context,
        _route((START, FINISH), (1000,)),
        selected,
        initial,
        reserve,
        None,
        None,
        RequestBudget(),
        limit,
        allow_repair=False,
    )


def test_repeated_road_facts_recalculate_prices_and_preserve_offer_ids():
    roads = Roads()
    router = CachedBaselineRouter(
        roads,
        BaselineRouteCache(),
        "profile-v1",
        cache_selected_routes=True,
    )
    context = PlanningContext(router, FakeCandidates())
    selected = tuple(
        ProjectedCandidate(s, Decimal(0), Decimal(0), s.station_id) for s in STATIONS
    )
    _, original, _ = verify(context, selected)
    changed = (
        replace(
            selected[0],
            station=replace(
                STATIONS[0], station_id="other-offer", price_per_gallon=Decimal(2)
            ),
        ),
        selected[1],
    )
    _, cheaper, _ = verify(context, changed)
    assert roads.calls == 1
    assert cheaper.exact_purchase_cost < original.exact_purchase_cost
    assert cheaper.visits[0].station_id == "other-offer"
    # Cached road distances do not override reserve or current policy.
    with pytest.raises(RouteCheckFailed):
        verify(context, selected, initial=Decimal(30))
    with pytest.raises(RouteCheckFailed):
        verify(context, selected, reserve=Decimal(15))
    with pytest.raises(DurationLimitExceeded):
        verify(context, selected, limit=Decimal(1))
    assert roads.calls == 1


def test_sequence_profile_and_expiry_invalidate_selected_road_facts():
    ticks = [0.0]
    cache = BaselineRouteCache(clock=lambda: ticks[0])
    roads = Roads()
    v1 = CachedBaselineRouter(roads, cache, "v1", cache_selected_routes=True)
    v2 = CachedBaselineRouter(roads, cache, "v2", cache_selected_routes=True)
    points = (START, STATIONS[0].coordinates, STATIONS[1].coordinates, FINISH)
    v1.route(points, RequestBudget())
    v1.route(points, RequestBudget())
    v1.route((START, *reversed(points[1:-1]), FINISH), RequestBudget())
    v2.route(points, RequestBudget())
    assert roads.calls == 3
    ticks[0] = 601
    v1.route(points, RequestBudget())
    assert roads.calls == 4


def test_cached_failed_snap_still_fails_after_another_offer_uses_the_point():
    class BadRoads(Roads):
        def route(self, points, budget):
            result = super().route(points, budget)
            return replace(
                result,
                waypoints=(
                    result.waypoints[0],
                    replace(result.waypoints[1], snap_distance_meters=Decimal(300)),
                    *result.waypoints[2:],
                ),
            )

    roads = BadRoads()
    router = CachedBaselineRouter(
        roads,
        BaselineRouteCache(),
        "v1",
        cache_selected_routes=True,
    )
    selected = tuple(
        ProjectedCandidate(s, Decimal(0), Decimal(0), s.station_id) for s in STATIONS
    )
    for _ in range(2):
        with pytest.raises(RouteCheckFailed):
            verify(PlanningContext(router, FakeCandidates()), selected)
    assert roads.calls == 1
