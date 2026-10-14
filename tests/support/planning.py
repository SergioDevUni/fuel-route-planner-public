from datetime import UTC, datetime
from decimal import Decimal

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.domain.units import METERS_PER_MILE
from app.planning.candidates import ProjectedCandidate, StationCandidate

START = Coordinates(40, -90)

FINISH = Coordinates(40, -70)


def _station(station_id: str, longitude: float, price: str) -> StationCandidate:
    return StationCandidate(
        station_id,
        station_id,
        "Main",
        "City",
        "IL",
        Coordinates(40, longitude),
        Decimal(price),
    )


STATIONS = (_station("1", -82, "3"), _station("2", -74, "4"))


def _route(
    points: tuple[Coordinates, ...],
    miles: tuple[int, ...],
    bad_index: int | None = None,
    seconds_per_leg: int = 100,
) -> Route:
    legs = tuple(
        RouteLeg(Decimal(miles_part) * METERS_PER_MILE, Decimal(seconds_per_leg))
        for miles_part in miles
    )
    waypoints = tuple(
        RouteWaypoint(point, point, index, Decimal(300 if index == bad_index else 0))
        for index, point in enumerate(points)
    )
    return Route(
        geometry=points,
        waypoints=waypoints,
        legs=legs,
        distance_meters=sum((leg.distance_meters for leg in legs), Decimal(0)),
        duration_seconds=Decimal(seconds_per_leg * len(legs)),
        engine_version=None,
        fetched_at=datetime.now(UTC),
    )


class FakeCandidates:
    version = "test"
    stations = STATIONS

    def along_route(self, route: Route, corridor_meters: float = 8000):
        return tuple(
            ProjectedCandidate(
                station,
                Decimal(progress) * METERS_PER_MILE,
                Decimal(100),
                f"{station.station_id}:1",
            )
            for station, progress in zip(STATIONS, (400, 800), strict=True)
        )

    def within_detour_envelope(self, route: Route, max_total_meters: Decimal):
        return ()


class FakeRouter:
    def __init__(self, bad_index: int | None = None, extra_miles: int = 0) -> None:
        self.calls = 0
        self.bad_index = bad_index
        self.extra_miles = extra_miles

    def route(self, points, budget):
        budget.consume_attempt()
        self.calls += 1
        if len(points) == 2:
            return _route(points, (1000,))
        if len(points) == 4:
            return _route(points, (400, 400, 200 + self.extra_miles), self.bad_index)
        return _route(points, (400, 600), self.bad_index)


class FakeMatrixRouter(FakeRouter):
    def matrix(self, points, budget):
        budget.consume_attempt()
        assert len(points) == 4

        def road(value: int) -> Decimal:
            return Decimal(value) * METERS_PER_MILE

        return RoadDistanceMatrix(
            (
                (Decimal(0), road(400), None, road(1000)),
                (None, Decimal(0), road(400), None),
                (None, None, Decimal(0), road(200)),
                (None, None, None, Decimal(0)),
            ),
            (Decimal(0),) * 4,
            (Decimal(0),) * 4,
            (
                (Decimal(0), Decimal(100), None, Decimal(100)),
                (None, Decimal(0), Decimal(100), None),
                (None, None, Decimal(0), Decimal(100)),
                (None, None, None, Decimal(0)),
            ),
        )
