from decimal import Decimal

from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.corridor_selection import compact_corridor, provisional_legs
from app.planning.initial_route import initial_corridor
from app.planning.trip_results import calculate_fuel
from tests.support.planning import FINISH, START, _route, _station


def offer(name, miles, price, offset=0):
    return ProjectedCandidate(
        _station(name, -90 + miles / 100, str(price)),
        Decimal(miles) * METERS_PER_MILE,
        Decimal(offset),
        name,
    )


def cost(baseline, items, initial=Decimal(50), reserve=Decimal(0)):
    return calculate_fuel(
        tuple(v.station for v in items),
        provisional_legs(baseline, items),
        initial,
        reserve,
    ).exact_purchase_cost


def test_initial_chain_keeps_cheaper_offers_instead_of_only_spacing_stops():
    baseline = _route((START, FINISH), (2800,))
    corridor = tuple(
        offer(str(i), i * 100, 2 if i % 4 == 3 else 5) for i in range(1, 28)
    )
    spaced = compact_corridor(baseline, corridor, Decimal(50), Decimal(0))
    result = initial_corridor(
        baseline, corridor, spaced, Decimal(50), Decimal(0), RequestBudget()
    )
    assert len(result) <= 12
    assert cost(baseline, result) < cost(baseline, spaced)
    assert all(item in corridor for item in result)


def test_pruning_preserves_necessary_first_stop_with_low_fuel_and_reserve():
    baseline = _route((START, FINISH), (1400,))
    corridor = (
        offer("first", 40, 5),
        *(offer(str(i), i * 100, 3) for i in range(1, 14)),
    )
    result = initial_corridor(
        baseline, corridor, (), Decimal(7), Decimal(2), RequestBudget()
    )
    assert result[0].station.station_id == "first"
    assert len(result) <= 12
    assert cost(baseline, result, Decimal(7), Decimal(2)) > 0


def test_access_cost_prevents_extra_detours_for_small_price_differences():
    baseline = _route((START, FINISH), (1000,))
    spaced = (offer("near-one", 400, 3), offer("near-two", 800, 3))
    remote = (
        offer("far-one", 400, "2.999", 5000),
        offer("far-two", 800, "2.999", 5000),
    )
    assert (
        initial_corridor(
            baseline, remote, spaced, Decimal(50), Decimal(0), RequestBudget()
        )
        == spaced
    )
