from decimal import Decimal

from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.corridor_selection import compact_corridor
from app.planning.trip import TripPlanner
from app.routing.client import RoutingUnavailable
from tests.support.planning import (
    FINISH,
    START,
    FakeCandidates,
    FakeRouter,
    _route,
    _station,
)


def test_compact_corridor_uses_fewer_csv_stops_with_provisional_fuel_range() -> None:
    baseline = _route((START, FINISH), (2800,))
    corridor = tuple(
        ProjectedCandidate(
            _station(str(index), -90 + index / 10, "3"),
            Decimal(index * 100) * METERS_PER_MILE,
            Decimal(100),
            "csv-corridor",
        )
        for index in range(1, 28)
    )
    compact = compact_corridor(baseline, corridor, Decimal(50), Decimal(0))
    assert 5 <= len(compact) <= 10
    assert all(item in corridor for item in compact)
    progresses = (
        (Decimal(0),)
        + tuple(item.progress_meters for item in compact)
        + (baseline.distance_meters,)
    )
    assert all(
        right - left <= Decimal(500) * METERS_PER_MILE
        for left, right in zip(progresses, progresses[1:], strict=False)
    )


def test_matrix_outage_and_slow_corridor_recover_with_compact_chain() -> None:
    class Candidates(FakeCandidates):
        stations = tuple(
            _station(str(i), longitude, str(i))
            for i, longitude in enumerate((-88, -82, -76, -74), 1)
        )

        def along_route(self, route, corridor_meters=8000):
            return tuple(
                ProjectedCandidate(
                    station,
                    Decimal(progress) * METERS_PER_MILE,
                    Decimal(500 if progress == 700 else 100),
                    station.station_id,
                )
                for station, progress in zip(
                    self.stations, (100, 400, 700, 800), strict=True
                )
            )

    class RoutingPort(FakeRouter):
        def matrix(self, points, budget):
            budget.consume_attempt()
            raise RoutingUnavailable("injected unavailable matrix")

        def route(self, points, budget):
            budget.consume_attempt()
            self.calls += 1
            progress = {-90: 0, -88: 100, -82: 400, -76: 700, -74: 800, -70: 1000}
            markers = [progress[p.longitude] for p in points]
            miles = tuple(b - a for a, b in zip(markers, markers[1:], strict=False))
            return _route(
                points, miles, seconds_per_leg=800 if len(points) >= 5 else 100
            )

    router = RoutingPort()
    budget = RequestBudget()
    trip = TripPlanner(router, Candidates(), router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal(3),
        matrix_first=True,
        budget=budget,
    )
    assert budget.attempts == 4
    assert budget.corrections == 1
    assert [s.station_id for s in trip.routed_stations] == ["2", "4"]
    assert trip.verified.duration_seconds <= trip.baseline.duration_seconds + 1800
    assert trip.fuel.ending_gallons >= 0
