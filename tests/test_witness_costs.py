import pytest

from app.domain.units import METERS_PER_MILE
from scripts.witness_costs import itinerary_cost


def test_independent_itinerary_lp_buys_only_remaining_need():
    legs = [str(METERS_PER_MILE * gallons * 10) for gallons in (3, 4)]
    assert itinerary_cost(legs, [3], 5, 2) == pytest.approx(12)


def test_independent_itinerary_rejects_unreachable_first_stop():
    legs = [str(METERS_PER_MILE * gallons * 10) for gallons in (6, 4)]
    with pytest.raises(ValueError, match="no optimum"):
        itinerary_cost(legs, [3], 5, 0)
