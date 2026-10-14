from decimal import Decimal

import httpx
import pytest

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates
from app.domain.units import METERS_PER_MILE
from app.planning.candidates import ProjectedCandidate
from app.planning.trip import TripPlanner
from app.routing.client import OpenRouteServiceAdapter, RoutingUnavailable
from tests.support.planning import (
    FINISH,
    START,
    STATIONS,
    FakeCandidates,
    FakeMatrixRouter,
    FakeRouter,
    _route,
    _station,
)


def test_matrix_first_repairs_rejected_waypoint_on_fourth_hosted_call() -> None:
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

    class RepairRouter(FakeRouter):
        def matrix(self, points, budget):
            budget.consume_attempt()
            progress = {-90: 0, -82: 400, -81: 450, -74: 800, -70: 1000}
            markers = [progress[point.longitude] for point in points]
            rows = tuple(
                tuple(
                    Decimal(finish - start) * METERS_PER_MILE
                    if finish >= start
                    else None
                    for finish in markers
                )
                for start in markers
            )
            return RoadDistanceMatrix(
                rows,
                (Decimal(0),) * len(points),
                (Decimal(0),) * len(points),
                tuple(
                    tuple(
                        None if value is None else value / METERS_PER_MILE * 60
                        for value in row
                    )
                    for row in rows
                ),
            )

        def route(self, points, budget):
            budget.consume_attempt()
            self.calls += 1
            progress = {-90: 0, -82: 400, -81: 450, -74: 800, -70: 1000}
            markers = [progress[point.longitude] for point in points]
            miles = tuple(b - a for a, b in zip(markers, markers[1:], strict=False))
            bad = 1 if self.calls == 2 and points[1].longitude == -82 else None
            return _route(points, miles, bad_index=bad)

    router = RepairRouter()
    candidates = Alternatives()
    trip = TripPlanner(router, candidates, matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        matrix_first=True,
    )
    assert trip.outbound_attempts == 4
    assert router.calls == 3
    assert trip.routed_stations[0].station_id == "3"


def test_matrix_failure_falls_back_within_three_attempts() -> None:
    class FailedMatrixRouter(FakeMatrixRouter):
        def matrix(self, points, budget):
            budget.consume_attempt()
            raise RoutingUnavailable("matrix unavailable")

    router = FailedMatrixRouter()
    trip = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
    )
    assert trip.selection_method == "road_corridor_selection"
    assert trip.outbound_attempts == 3


def test_matrix_first_failure_does_not_repeat_hosted_matrix() -> None:
    class FailedMatrixRouter(FakeMatrixRouter):
        matrix_calls = 0

        def matrix(self, points, budget):
            budget.consume_attempt()
            self.matrix_calls += 1
            raise RoutingUnavailable("matrix unavailable")

    router = FailedMatrixRouter()
    trip = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        matrix_first=True,
    )
    assert trip.selection_method == "road_corridor_recovery"
    assert trip.outbound_attempts == 3
    assert router.matrix_calls == 1
    assert router.calls == 2


@pytest.mark.parametrize("fault", (429, 503, "timeout"))
def test_provider_matrix_fault_retains_verified_corridor_incumbent(fault) -> None:
    requests = []

    def failed_provider(request):
        requests.append(request.url.path)
        if fault == "timeout":
            raise httpx.ReadTimeout("simulated provider timeout", request=request)
        return httpx.Response(fault, json={"error": {"code": None}})

    router = FakeRouter()
    with httpx.Client(transport=httpx.MockTransport(failed_provider)) as client:
        provider = OpenRouteServiceAdapter("test-key", client)
        trip = TripPlanner(router, FakeCandidates(), matrix_provider=provider).plan(
            START,
            FINISH,
            Decimal(50),
            Decimal(0),
            None,
            None,
            Decimal("3.20"),
            matrix_first=True,
        )
    assert trip.selection_method == "road_corridor_recovery"
    assert trip.outbound_attempts == 3
    assert len(requests) == 1
    assert router.calls == 2
    assert trip.fuel.exact_purchase_cost > 0


def test_failed_matrix_can_repair_bad_waypoint_on_fourth_call() -> None:
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

    class FailedMatrixRouter(FakeRouter):
        def matrix(self, points, budget):
            budget.consume_attempt()
            raise RoutingUnavailable("matrix unavailable")

        def route(self, points, budget):
            budget.consume_attempt()
            self.calls += 1
            progress = {-90: 0, -82: 400, -81: 450, -74: 800, -70: 1000}
            markers = [progress[point.longitude] for point in points]
            miles = tuple(b - a for a, b in zip(markers, markers[1:], strict=False))
            bad = 1 if self.calls == 2 and points[1].longitude == -82 else None
            return _route(points, miles, bad_index=bad)

    router = FailedMatrixRouter()
    trip = TripPlanner(router, Alternatives(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        matrix_first=True,
    )
    assert trip.outbound_attempts == 4
    assert router.calls == 3
    assert trip.routed_stations[0].station_id == "3"


def test_short_no_stop_trip_uses_one_hosted_direction_call() -> None:
    class ShortRouter(FakeMatrixRouter):
        matrix_calls = 0

        def matrix(self, points, budget):
            self.matrix_calls += 1
            raise AssertionError("short direct trip should skip matrix")

        def route(self, points, budget):
            budget.consume_attempt()
            self.calls += 1
            return _route(points, (100,))

    router = ShortRouter()
    trip = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        Coordinates(40, -89),
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        matrix_first=True,
    )
    assert trip.selection_method == "no_stop"
    assert trip.outbound_attempts == router.calls == 1
    assert router.matrix_calls == 0
