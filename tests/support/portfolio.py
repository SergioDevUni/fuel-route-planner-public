from datetime import UTC, datetime
from decimal import Decimal

from app.domain.fuel import FuelOpportunity
from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.planning.candidates import StationCandidate
from app.planning.fuel import optimize_fuel
from app.planning.ledger import value_fuel_plan
from app.planning.trip_types import PlannedTrip

START = Coordinates(40, -90)

STOP = Coordinates(40, -85)

FINISH = Coordinates(40, -80)


def _plan(
    price: str, method: str, station_id: str = "csv-1", *, initial: str = "2"
) -> PlannedTrip:
    station = StationCandidate(
        station_id, "CSV station", "Highway", "City", "IL", STOP, Decimal(price)
    )
    points = (START, STOP, FINISH)
    legs = (RouteLeg(Decimal(30_000), Decimal(4000)),) * 2
    route = Route(
        points,
        tuple(
            RouteWaypoint(point, point, index, Decimal(0))
            for index, point in enumerate(points)
        ),
        legs,
        Decimal(60_000),
        Decimal(8000),
        "profile",
        datetime.now(UTC),
    )
    fuel = optimize_fuel(
        (FuelOpportunity(station.station_id, station.price_per_gallon),),
        tuple(leg.distance_meters for leg in legs),
        Decimal(initial),
        Decimal(0),
    )
    return PlannedTrip(
        route,
        route,
        (station,),
        fuel,
        value_fuel_plan(
            fuel,
            tuple(leg.distance_meters for leg in legs),
            Decimal(initial),
            Decimal(3),
        ),
        Decimal(3),
        "user_supplied",
        "csv-v1",
        0,
        method,
    )
