"""Independent checks of public trip fuel and route invariants."""

from decimal import Decimal

from app.domain.ledger import usd_cents


def validate_verified_trip(payload: dict, csv_ids: set[str]) -> list[str]:
    """Check public route, fuel, purchase, and CSV identity invariants."""
    errors: list[str] = []
    route = payload["route"]
    vehicle = payload["vehicle"]
    costs = payload["costs"]
    stops = payload["fuel_stops"]
    legs = route["legs"]
    tolerance = Decimal("0.000001")
    if (
        route["geometry"]["type"] != "LineString"
        or len(route["geometry"]["coordinates"]) < 2
    ):
        errors.append("route geometry is missing")
    if not legs:
        errors.append("road legs are missing")
    if Decimal(route["extra_duration_seconds"]) > Decimal(1800):
        errors.append("travel time exceeds 30 extra minutes")
    if not payload.get("map_url"):
        errors.append("map URL is missing")
    balance = Decimal(vehicle["initial_fuel_gallons"])
    reserve = Decimal(vehicle["reserve_gallons"])
    purchased_from_legs = Decimal(0)
    for index, leg in enumerate(legs, start=1):
        miles = Decimal(leg["distance_miles"])
        consumed = Decimal(leg["fuel_consumed_gallons"])
        arrival = Decimal(leg["arrival_fuel_gallons"])
        departure = Decimal(leg["departure_fuel_gallons"])
        if miles > 500 + tolerance or miles < 0:
            errors.append(f"leg {index} exceeds vehicle range")
        if abs(consumed - miles / 10) > tolerance:
            errors.append(f"leg {index} fuel use differs from 10 mpg")
        if abs(arrival - (balance - consumed)) > tolerance:
            errors.append(f"leg {index} fuel balance is inconsistent")
        if arrival < reserve - tolerance or departure > 50 + tolerance:
            errors.append(f"leg {index} violates tank or reserve")
        if departure < arrival - tolerance:
            errors.append(f"leg {index} loses fuel at a stop")
        purchased_from_legs += max(Decimal(0), departure - arrival)
        balance = departure
    if abs(balance - Decimal(costs["ending_fuel_gallons"])) > tolerance:
        errors.append("ending fuel does not reconcile")
    purchased_from_stops = sum(
        (Decimal(stop["purchase_gallons"]) for stop in stops), Decimal(0)
    )
    if abs(purchased_from_legs - purchased_from_stops) > tolerance:
        errors.append("fuel purchases do not reconcile with road legs")
    if abs(purchased_from_stops - Decimal(costs["fuel_purchased_gallons"])) > tolerance:
        errors.append("fuel purchased total does not reconcile")
    exact_cost = sum(
        (
            Decimal(stop["purchase_gallons"]) * Decimal(stop["price_per_gallon"])
            for stop in stops
        ),
        Decimal(0),
    )
    if abs(usd_cents(exact_cost) - Decimal(costs["fuel_purchases_cost"])) > Decimal(
        "0.01"
    ):
        errors.append("money spent does not reconcile with stop purchases")
    for stop in stops:
        if stop["station_id"] not in csv_ids:
            errors.append(f"stop {stop['station_id']} is not a CSV station")
    return errors
