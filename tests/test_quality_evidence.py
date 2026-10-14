from dataclasses import replace
from decimal import Decimal
from itertools import permutations
from math import ceil

from app.domain.routing import Coordinates
from app.domain.units import METERS_PER_MILE
from app.planning.quality_evidence import offer_coverage, purchase_bound
from app.routing.geometry import geodesic_meters
from tests.support.oracle import independent_path_cost
from tests.support.planning import STATIONS


def test_bound_includes_cheapest_offer_outside_selected_search():
    start, finish = Coordinates(32, -100), Coordinates(32, -80)
    expensive = replace(STATIONS[0], price_per_gallon=Decimal(5))
    cheap = replace(STATIONS[1], price_per_gallon=Decimal(1))
    args = (Decimal(0), Decimal(0), Decimal(1000))
    limited = purchase_bound(start, finish, [expensive], *args)
    nationwide = purchase_bound(start, finish, [expensive, cheap], *args)
    assert Decimal(nationwide["lower_bound_usd"]) < Decimal(limited["lower_bound_usd"])
    assert nationwide["priced_offer_count"] == 2


def test_zero_bound_has_no_division_by_zero_or_cost_search_claim():
    point = Coordinates(32, -100)
    evidence = purchase_bound(
        point, point, STATIONS, Decimal(50), Decimal(0), Decimal(0)
    )
    assert Decimal(evidence["lower_bound_usd"]) == 0
    assert evidence["relative_gap_percent"] is None


def test_offer_accounting_preserves_shared_points_and_unknown_scope():
    evidence = offer_coverage(
        ["shared-a", "shared-b", "pending", "cost-excluded"],
        ["shared-a", "shared-b", "pending"],
        ["shared-a", "shared-b"],
        ["shared-b", "pending"],
    )
    assert evidence == {
        "loaded_csv_offers": 4,
        "safely_excluded_by_cost": 1,
        "eligible_csv_offers": 3,
        "retained_graph_offers": 2,
        "additional_eligible_offers": 1,
        "retained_offers_awaiting_road_data": 1,
    }


def test_relaxed_bound_is_below_independent_enumerated_fuel_lp():
    start, finish = Coordinates(32, -100), Coordinates(32, -90)
    stations = tuple(
        replace(station, coordinates=Coordinates(32, longitude))
        for station, longitude in zip(STATIONS, (-96, -92), strict=True)
    )
    initial, reserve = 30, 2
    prices = tuple(int(s.price_per_gallon) for s in stations)
    feasible_costs = []
    for count in range(3):
        for stops in permutations(range(2), count):
            points = (start, *(stations[i].coordinates for i in stops), finish)
            miles = [
                ceil(geodesic_meters(a, b) / METERS_PER_MILE)
                for a, b in zip(points, points[1:], strict=False)
            ]
            cost = independent_path_cost(
                tuple(prices[i] for i in stops), miles, initial, reserve
            )
            if cost is not None:
                feasible_costs.append(cost)
    assert feasible_costs
    best = Decimal(str(min(feasible_costs)))
    evidence = purchase_bound(
        start, finish, stations, Decimal(initial), Decimal(reserve), best
    )
    assert Decimal(evidence["lower_bound_usd"]) <= best
