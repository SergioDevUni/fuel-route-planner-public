from datetime import UTC, datetime
from decimal import Decimal

from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint


def _route() -> Route:
    points = (
        Coordinates(41.0, -88.0),
        Coordinates(41.0, -87.9),
        Coordinates(41.0, -87.8),
    )
    return Route(
        geometry=points,
        waypoints=(
            RouteWaypoint(points[0], points[0], 0, Decimal(0)),
            RouteWaypoint(points[-1], points[-1], 2, Decimal(0)),
        ),
        legs=(RouteLeg(Decimal(20000), Decimal(1000)),),
        distance_meters=Decimal(20000),
        duration_seconds=Decimal(1000),
        engine_version=None,
        fetched_at=datetime.now(UTC),
    )
