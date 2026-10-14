from decimal import Decimal

import pytest

from app.domain.routing import Coordinates
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate, StationCandidate
from app.planning.trip import TripPlanner
from app.planning.trip_types import (
    DetourLimitExceeded,
    DurationLimitExceeded,
    NoFeasiblePlan,
)
from tests.support.planning import (
    FINISH,
    START,
    STATIONS,
    FakeCandidates,
    FakeRouter,
    _route,
    _station,
)


def test_bad_selected_snap_does_not_return_a_successful_trip() -> None:
    router = FakeRouter(bad_index=1)
    with pytest.raises(NoFeasiblePlan):
        TripPlanner(router, FakeCandidates()).plan(
            START,
            FINISH,
            Decimal(50),
            Decimal(0),
            Decimal(20),
            Decimal("0.1"),
            Decimal("3.20"),
        )
    assert router.calls <= 3


def test_selected_locality_uses_representative_road_snap_limit() -> None:
    locality = StationCandidate(
        "1",
        "1",
        "Main",
        "City",
        "IL",
        Coordinates(40, -82),
        Decimal(3),
        "city_representative",
    )
    selected = (
        ProjectedCandidate(locality, Decimal(400) * METERS_PER_MILE, Decimal(0), "1"),
        ProjectedCandidate(
            STATIONS[1], Decimal(800) * METERS_PER_MILE, Decimal(0), "2"
        ),
    )
    baseline = _route((START, FINISH), (1000,))
    router = FakeRouter(bad_index=1)
    from app.planning.route_verification import verify_selection
    from app.planning.trip_context import PlanningContext

    route, _, retained = verify_selection(
        PlanningContext(router, FakeCandidates()),
        baseline,
        selected,
        Decimal(50),
        Decimal(0),
        None,
        None,
        RequestBudget(),
        None,
    )
    assert retained == selected
    assert route.waypoints[1].snap_distance_meters == 300


def test_verified_detour_cap_is_enforced() -> None:
    router = FakeRouter(extra_miles=30)
    with pytest.raises(DetourLimitExceeded):
        TripPlanner(router, FakeCandidates()).plan(
            START,
            FINISH,
            Decimal(50),
            Decimal(0),
            Decimal(20),
            Decimal("0.1"),
            Decimal("3.20"),
        )
    assert router.calls <= 3


def test_balanced_time_only_accepts_more_than_old_distance_cap() -> None:
    router = FakeRouter(extra_miles=30)
    trip = TripPlanner(router, FakeCandidates()).plan(
        START, FINISH, Decimal(50), Decimal(0), None, None, Decimal("3.20")
    )
    assert (
        trip.verified.distance_meters - trip.baseline.distance_meters
        == 30 * METERS_PER_MILE
    )
    assert trip.verified.duration_seconds - trip.baseline.duration_seconds < 30 * 60
    assert router.calls == 2


def test_custom_time_limit_is_checked_against_verified_route() -> None:
    router = FakeRouter()
    with pytest.raises(DurationLimitExceeded):
        TripPlanner(router, FakeCandidates()).plan(
            START,
            FINISH,
            Decimal(50),
            Decimal(0),
            Decimal(20),
            Decimal("0.1"),
            Decimal("3.20"),
            "balanced",
            Decimal(1),
        )
    assert router.calls <= 3


def test_lowest_fuel_cost_mode_still_enforces_thirty_minute_ceiling() -> None:
    class SlowStopRouter(FakeRouter):
        def route(self, points, budget):
            route = super().route(points, budget)
            if len(points) == 2:
                return route
            return _route(points, (400, 400, 200), seconds_per_leg=700)

    router = SlowStopRouter()
    with pytest.raises(DurationLimitExceeded):
        TripPlanner(router, FakeCandidates()).plan(
            START,
            FINISH,
            Decimal(50),
            Decimal(0),
            None,
            None,
            Decimal("3.20"),
            "lowest_fuel_cost",
        )
    assert router.calls <= 3


def test_one_correction_replaces_bad_site_and_verifies_new_route() -> None:
    alternative = _station("3", -81, "3.1")

    class Alternatives(FakeCandidates):
        stations = (STATIONS[0], alternative, STATIONS[1])

        def along_route(self, route, corridor_meters=8000):
            return tuple(
                ProjectedCandidate(
                    station,
                    Decimal(progress) * METERS_PER_MILE,
                    Decimal(100),
                    f"{station.station_id}:1",
                )
                for station, progress in zip(
                    self.stations, (400, 450, 800), strict=True
                )
            )

    class CorrectionRouter(FakeRouter):
        def route(self, points, budget):
            budget.consume_attempt()
            self.calls += 1
            progress = {-90: 0, -82: 400, -81: 450, -74: 800, -70: 1000}
            markers = [progress[point.longitude] for point in points]
            miles = tuple(b - a for a, b in zip(markers, markers[1:], strict=False))
            return _route(points, miles, bad_index=1 if self.calls == 2 else None)

    router = CorrectionRouter()
    trip = TripPlanner(router, Alternatives()).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
    )
    assert router.calls == 3
    assert [item.station_id for item in trip.routed_stations] == ["3", "2"]
    assert trip.outbound_attempts == 3
