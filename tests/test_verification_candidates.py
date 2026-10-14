"""Currency-equivalent proposals keep fuel slack without changing exact search."""

from decimal import Decimal

from app.domain.fuel import FuelOpportunity
from app.domain.units import METERS_PER_MILE
from app.planning.fuel import optimize_fuel
from app.planning.fuel_graph import GraphFuelSolution
from app.planning.matrix_selection import rank_verification_candidate


def solution(ids, miles, price):
    distances = tuple(Decimal(m) * METERS_PER_MILE for m in miles)
    fuel = optimize_fuel(
        tuple(FuelOpportunity(s, Decimal(price)) for s in ids),
        distances,
        Decimal(50),
        Decimal(0),
    )
    return GraphFuelSolution(ids, distances, fuel, sum(distances, Decimal(0)))


def test_subcent_improvement_cannot_displace_a_currency_equal_slack_candidate():
    safe = solution(("safe-a", "safe-b"), (400, 300, 350), "3.00000003")
    fragile = solution(("tight-a", "tight-b"), (500, 500, 50), "3")
    assert safe.purchase_cost > fragile.purchase_cost
    assert rank_verification_candidate(
        safe, Decimal(50), Decimal(0)
    ) < rank_verification_candidate(fragile, Decimal(50), Decimal(0))
    # A genuinely lower displayed spending tier still has priority.
    cheaper = solution(("cheap-a", "cheap-b"), (500, 500, 50), "2.99")
    assert rank_verification_candidate(
        cheaper, Decimal(50), Decimal(0)
    ) < rank_verification_candidate(safe, Decimal(50), Decimal(0))
