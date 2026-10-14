"""Conservative nationwide CSV-offer screening."""

from decimal import Decimal
from itertools import permutations
from unittest.mock import patch

from app.domain.fuel import FuelOpportunity, VehicleSpec
from app.domain.routing import Coordinates
from app.planning.candidates import StationCandidate
from app.planning.fuel import FuelShortfall, optimize_fuel
from app.planning.national_scope import (
    _price_access_bound,
    screen_csv_offers,
)
from app.routing.geometry import geodesic_meters


def _station(station_id: str, longitude: float, price: str) -> StationCandidate:
    return StationCandidate(
        station_id,
        station_id,
        "CSV address",
        "CSV city",
        "IL",
        Coordinates(40.0, longitude),
        Decimal(price),
    )


def test_screen_keeps_shared_point_offers_and_cheap_far_option() -> None:
    start = Coordinates(40.0, -90.0)
    finish = Coordinates(40.0, -89.0)
    stations = (
        _station("first", -89.5, "3.20"),
        _station("second", -89.5, "2.90"),
        _station("far", -80.0, "2.50"),
    )
    result = screen_csv_offers(
        stations, start, finish, Decimal(5), Decimal(0), Decimal(20)
    )
    assert {item.station.station_id for item in result.possible_offers} == {
        "first",
        "second",
    }
    assert result.excluded_by_cost == 1
    assert result.total_csv_offers == 3


def test_screen_counts_reserve_and_retains_equal_cost_bound() -> None:
    point = Coordinates(40.0, -90.0)
    stations = (_station("same", -90.0, "3"),)
    with patch(
        "app.planning.national_scope.geodesic_meters",
        return_value=Decimal("80467.2"),
    ):
        result = screen_csv_offers(
            stations,
            point,
            point,
            Decimal(1),
            Decimal(1),
            Decimal(30),
            account_for_snaps=False,
        )
    assert result.excluded_by_cost == 0
    assert result.possible_offers[0].minimum_purchase_cost == Decimal(30)


def test_price_access_bound_charges_for_fuel_before_reaching_cheapest_site() -> None:
    vehicle = VehicleSpec()
    stations = (_station("far-cheap", -80, "1"), _station("near", -90, "3"))
    bound = _price_access_bound(
        stations,
        (Decimal(200) * Decimal("1609.344"), Decimal(0)),
        Decimal(0),
        vehicle,
    )
    assert bound.cost(Decimal(30)) == Decimal(70)
    assert bound.cost(Decimal(10)) == Decimal(30)


def test_price_access_screen_keeps_stops_in_feasible_sampled_paths() -> None:
    start = Coordinates(40, -90)
    finish = Coordinates(40, -88)
    stations = (
        _station("near", -89.9, "3"),
        _station("cheap", -89.4, "1"),
        _station("far", -88.5, "4"),
    )
    checked = 0
    for length in range(1, 4):
        for ordered in permutations(stations, length):
            points = (start, *(item.coordinates for item in ordered), finish)
            legs = tuple(
                geodesic_meters(first, second)
                for first, second in zip(points, points[1:], strict=False)
            )
            try:
                plan = optimize_fuel(
                    tuple(
                        FuelOpportunity(item.station_id, item.price_per_gallon)
                        for item in ordered
                    ),
                    legs,
                    Decimal(5),
                )
            except FuelShortfall:
                continue
            scope = screen_csv_offers(
                stations,
                start,
                finish,
                Decimal(5),
                Decimal(0),
                plan.exact_purchase_cost,
                price_access_bound=True,
            )
            retained = {item.station.station_id for item in scope.possible_offers}
            assert {item.station_id for item in ordered} <= retained
            checked += 1
    assert checked >= 3


def test_cost_screen_relaxes_allowed_road_snaps_at_the_cash_boundary():
    point = Coordinates(40, -90)
    stations = (_station("site", -90, "3"),)
    with patch(
        "app.planning.national_scope.geodesic_meters", return_value=Decimal(81467.2)
    ):
        ideal = screen_csv_offers(
            stations,
            point,
            point,
            Decimal(1),
            Decimal(1),
            Decimal(30),
            account_for_snaps=False,
        )
        road = screen_csv_offers(
            stations, point, point, Decimal(1), Decimal(1), Decimal(30)
        )
    assert ideal.excluded_by_cost == 1
    assert road.excluded_by_cost == 0
