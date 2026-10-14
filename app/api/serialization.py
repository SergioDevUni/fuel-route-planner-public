"""Public trip serialization independent of Django and station persistence."""

from decimal import Decimal
from typing import Any

from app.api.validation import TripInput
from app.domain.ledger import usd_cents
from app.domain.location import ResolvedLocation
from app.domain.routing import Coordinates
from app.domain.units import METERS_PER_MILE
from app.planning.trip_types import PlannedTrip, RouteCheckFailed


def _point(point: Coordinates) -> dict[str, Any]:
    return {"latitude": point.latitude, "longitude": point.longitude}


def _resolved(location: ResolvedLocation, routed: Coordinates) -> dict[str, Any]:
    return {
        "coordinates": _point(location.coordinates),
        "routing_coordinates": _point(routed),
        "precision": location.precision,
        "state": location.state,
        "place_id": location.place_id,
        "name": location.name,
    }


def _string(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.00000001"))
    # Fuel arithmetic tolerates sub-precision residuals; public zero has no sign.
    return format(rounded.copy_abs() if rounded.is_zero() else rounded, "f")


def public_trip_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Present trip details consistently across new and saved results."""
    result = dict(payload)
    assumptions = payload.get("assumptions")
    if not isinstance(assumptions, dict):
        return result
    result["assumptions"] = {
        key: value
        for key, value in assumptions.items()
        if key not in {"optimization_evidence", "optimization_interrupted"}
    }
    if "station_selection" in assumptions:
        result["assumptions"]["station_selection"] = "fuel_cost"
    return result


def serialize_trip(
    trip: TripInput,
    start: ResolvedLocation,
    finish: ResolvedLocation,
    result: PlannedTrip,
) -> dict[str, Any]:
    route = result.verified
    if route.waypoints[0].snap_distance_meters > Decimal(1000) or route.waypoints[
        -1
    ].snap_distance_meters > Decimal(1000):
        raise RouteCheckFailed("endpoint road snap exceeds policy")
    route_legs = []
    fuel_balance = trip.initial_fuel_gallons
    for index, leg in enumerate(route.legs):
        consumed = leg.distance_meters / METERS_PER_MILE / 10
        arrival = fuel_balance - consumed
        if index < len(result.fuel.visits):
            departure = result.fuel.visits[index].departure_gallons
        else:
            departure = arrival
        route_legs.append(
            {
                "sequence": index + 1,
                "distance_miles": _string(leg.distance_meters / METERS_PER_MILE),
                "fuel_consumed_gallons": _string(consumed),
                "arrival_fuel_gallons": _string(arrival),
                "departure_fuel_gallons": _string(departure),
            }
        )
        fuel_balance = departure
    stops: list[dict[str, Any]] = []
    for index, (station, visit) in enumerate(
        zip(result.routed_stations, result.fuel.visits, strict=True)
    ):
        if visit.purchased_gallons <= 0:
            continue
        stops.append(
            {
                "sequence": len(stops) + 1,
                "station_id": station.station_id,
                "name": station.name,
                "address": station.address,
                "city": station.city,
                "state": station.state,
                "coordinates": _point(route.waypoints[index + 1].routed),
                "price_per_gallon": _string(station.price_per_gallon),
                "arrival_fuel_gallons": _string(visit.arrival_gallons),
                "purchase_gallons": _string(visit.purchased_gallons),
                "departure_fuel_gallons": _string(visit.departure_gallons),
                "purchase_cost": _string(usd_cents(visit.exact_cost)),
            }
        )
    extra = max(Decimal(0), route.distance_meters - result.baseline.distance_meters)
    ledger = result.ledger
    return {
        "schema_version": "1.0",
        "status": "verified",
        "start": _resolved(start, route.waypoints[0].routed),
        "finish": _resolved(finish, route.waypoints[-1].routed),
        "vehicle": {
            "range_miles": 500,
            "miles_per_gallon": 10,
            "capacity_gallons": 50,
            "initial_fuel_gallons": _string(trip.initial_fuel_gallons),
            "reserve_gallons": _string(trip.reserve_gallons),
        },
        "route": {
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    list(point.as_provider_pair()) for point in route.geometry
                ],
            },
            "distance_miles": _string(route.distance_meters / METERS_PER_MILE),
            "duration_seconds": _string(route.duration_seconds),
            "baseline_duration_seconds": _string(result.baseline.duration_seconds),
            "extra_duration_seconds": _string(
                max(
                    Decimal(0),
                    route.duration_seconds - result.baseline.duration_seconds,
                )
            ),
            "baseline_distance_miles": _string(
                result.baseline.distance_meters / METERS_PER_MILE
            ),
            "extra_distance_miles": _string(extra / METERS_PER_MILE),
            "legs": route_legs,
        },
        "fuel_stops": stops,
        "costs": {
            "currency": "USD",
            "fuel_purchased_gallons": _string(ledger.purchased_gallons),
            "fuel_consumed_gallons": _string(ledger.consumed_gallons),
            "ending_fuel_gallons": _string(ledger.ending_gallons),
            "fuel_purchases_cost": _string(ledger.fuel_purchases_cost),
            "total_fuel_cost": _string(ledger.total_fuel_cost),
            "initial_fuel_unit_price": _string(result.initial_price)
            if result.initial_price
            else None,
            "initial_fuel_price_basis": result.initial_price_basis,
            "is_estimate": True,
        },
        "assumptions": {
            "fuel_product": "unspecified_assumed_compatible",
            "station_selection": "fuel_cost",
            "road_legs_verified": True,
            "optimization_mode": trip.optimization_mode,
            "max_extra_duration_minutes": _string(trip.max_extra_duration_minutes),
        },
    }


def zero_distance_payload(
    trip: TripInput, start: ResolvedLocation, finish: ResolvedLocation
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "zero_distance",
        "start": _resolved(start, start.coordinates),
        "finish": _resolved(finish, finish.coordinates),
        "route": {
            "geometry": {
                "type": "Point",
                "coordinates": list(start.coordinates.as_provider_pair()),
            },
            "distance_miles": "0.00000000",
            "duration_seconds": "0.00000000",
            "baseline_duration_seconds": "0.00000000",
            "extra_duration_seconds": "0.00000000",
            "legs": [],
        },
        "fuel_stops": [],
        "costs": {
            "currency": "USD",
            "fuel_purchases_cost": "0.00000000",
            "total_fuel_cost": "0.00000000",
            "ending_fuel_gallons": _string(trip.initial_fuel_gallons),
        },
        "assumptions": {
            "optimization_mode": trip.optimization_mode,
            "max_extra_duration_minutes": _string(trip.max_extra_duration_minutes),
        },
    }
