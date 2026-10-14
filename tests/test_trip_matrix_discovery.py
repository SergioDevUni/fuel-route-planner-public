from decimal import Decimal

import pytest

from app.domain.road import RoadDistanceMatrix
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.matrix_cache import CachedRoadMatrixProvider
from app.planning.trip import TripPlanner
from app.planning.trip_types import (
    NoFeasiblePlan,
    RouteCheckFailed,
)
from app.routing.local_osrm import RoadMatrix
from app.routing.regional_matrix import RegionalRoadMatrixProvider
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


def test_empty_matrix_graph_fetches_road_baseline_and_repairs_bad_stop() -> None:
    alternative = _station("3", -81, "3.1")

    class Alternatives(FakeCandidates):
        stations = (STATIONS[0], alternative, STATIONS[1])
        saw_road_baseline = False

        def along_route(self, route, corridor_meters=8000):
            if (
                route.engine_version is None
                and route.distance_meters == 1000 * METERS_PER_MILE
            ):
                self.saw_road_baseline = True
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

    class EmptyGraphRouter(FakeRouter):
        def matrix(self, points, budget):
            budget.consume_attempt()
            size = len(points)
            distances = tuple(
                tuple(
                    Decimal(1000) * METERS_PER_MILE
                    if row == 0 and col == size - 1
                    else Decimal(0)
                    if row == col
                    else None
                    for col in range(size)
                )
                for row in range(size)
            )
            durations = tuple(
                tuple(
                    Decimal(100)
                    if row == 0 and col == size - 1
                    else Decimal(0)
                    if row == col
                    else None
                    for col in range(size)
                )
                for row in range(size)
            )
            return RoadDistanceMatrix(
                distances,
                (Decimal(0),) * size,
                (Decimal(0),) * size,
                durations,
            )

        def route(self, points, budget):
            budget.consume_attempt()
            self.calls += 1
            progress = {-90: 0, -82: 400, -81: 450, -74: 800, -70: 1000}
            markers = [progress[point.longitude] for point in points]
            miles = tuple(b - a for a, b in zip(markers, markers[1:], strict=False))
            bad = 1 if self.calls == 2 and points[1].longitude == -82 else None
            return _route(points, miles, bad_index=bad)

    router = EmptyGraphRouter()
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
    assert router.calls == 3
    assert trip.outbound_attempts == 4
    assert candidates.saw_road_baseline
    assert trip.selection_method == "road_corridor_recovery"
    assert trip.matrix_search_complete is False
    assert trip.routed_stations[0].station_id == "3"


def test_matrix_path_rejects_bad_final_snap_without_alternative() -> None:
    router = FakeMatrixRouter(bad_index=1)
    with pytest.raises((RouteCheckFailed, NoFeasiblePlan)):
        TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
            START,
            FINISH,
            Decimal(50),
            Decimal(0),
            Decimal(20),
            Decimal("0.1"),
            Decimal("3.20"),
        )
    assert router.calls == 2


def test_matrix_can_find_chain_when_narrow_corridor_has_none() -> None:
    class WideOnlyCandidates(FakeCandidates):
        def along_route(self, route, corridor_meters=8000):
            if corridor_meters <= 32_000:
                return ()
            return super().along_route(route, corridor_meters)

    router = FakeMatrixRouter()
    trip = TripPlanner(router, WideOnlyCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
    )
    assert trip.selection_method == "bounded_directed_matrix_graph"
    assert [item.station_id for item in trip.routed_stations] == ["1", "2"]
    assert trip.outbound_attempts == 3


def test_matrix_can_find_chain_from_alternate_road_envelope() -> None:
    class AlternateOnlyCandidates(FakeCandidates):
        def along_route(self, route, corridor_meters=8000):
            return ()

        def within_detour_envelope(self, route, max_total_meters):
            return super().along_route(route, 80_000)

    router = FakeMatrixRouter()
    trip = TripPlanner(router, AlternateOnlyCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
    )
    assert trip.selection_method == "bounded_directed_matrix_graph"
    assert [item.station_id for item in trip.routed_stations] == ["1", "2"]
    assert trip.outbound_attempts == 3


@pytest.mark.parametrize("matrix_source", ["cached", "regional"])
def test_matrix_without_hosted_attempt_leaves_room_for_repair(
    tmp_path, matrix_source
) -> None:
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
                    Decimal(max(0, finish - start)) * METERS_PER_MILE
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
                        None if value is None else Decimal(100 if value else 0)
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
    matrix_points = (
        START,
        STATIONS[0].coordinates,
        alternative.coordinates,
        STATIONS[1].coordinates,
        FINISH,
    )
    if matrix_source == "cached":
        provider = CachedRoadMatrixProvider(router, tmp_path, clock=lambda: 1000)
        provider.matrix(matrix_points, RequestBudget())  # Warm before the trip.
    else:

        class LocalMatrix:
            def matrix(self, points):
                matrix = router.matrix(points, RequestBudget())
                return RoadMatrix(
                    tuple(
                        tuple(
                            float(value) if value is not None else None for value in row
                        )
                        for row in matrix.distances_meters
                    ),
                    (0.0,) * len(points),
                    "regional-fixture",
                    durations_seconds=tuple(
                        tuple(
                            float(value) if value is not None else None for value in row
                        )
                        for row in matrix.durations_seconds
                    ),
                    destination_snap_meters=(0.0,) * len(points),
                )

        provider = RegionalRoadMatrixProvider(LocalMatrix(), router, (-91, 39, -69, 41))
    trip = TripPlanner(router, Alternatives(), matrix_provider=provider).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
    )
    assert [station.station_id for station in trip.routed_stations] == ["3", "2"]
    assert trip.outbound_attempts == 3
    assert router.calls == 3
