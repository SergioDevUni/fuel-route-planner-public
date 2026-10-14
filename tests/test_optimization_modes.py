"""Mode tradeoffs against measured roads and absolute request deadlines."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.trip import TripPlanner
from app.planning.verification_timing import VerificationTiming
from tests.support.planning import (
    FINISH,
    START,
    FakeCandidates,
    FakeMatrixRouter,
    _route,
)


@pytest.mark.parametrize(
    ("mode", "expected_cost", "directions", "elapsed", "bad_stop"),
    [
        ("balanced", 160, 2, 5.0, False),
        ("lowest_fuel_cost", 120, 3, 10.5, False),
        ("lowest_fuel_cost", 160, 3, 10.5, True),
    ],
)
def test_fuel_mode_can_verify_a_cheaper_trip_after_balanced_improvement_ends(
    monkeypatch, mode, expected_cost, directions, elapsed, bad_stop
):
    monkeypatch.setattr(
        "app.planning.matrix_selection.verification_timing", VerificationTiming()
    )
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    budget.reserve_finalization(1.5)

    class MeasuredRoads(FakeMatrixRouter):
        def route(self, points, budget):
            result = super().route(points, budget)
            if len(points) == 3:
                result = _route(points, (400, 500), bad_index=1 if bad_stop else None)
            ticks[0] += 2.5
            return result

        def matrix(self, points, budget):
            result = super().matrix(points, budget)
            distances = [list(row) for row in result.distances_meters]
            durations = [list(row) for row in result.durations_seconds]
            distances[1][-1] = Decimal(500) * METERS_PER_MILE
            durations[1][-1] = Decimal(100)
            ticks[0] += 3
            return replace(
                result,
                distances_meters=tuple(tuple(row) for row in distances),
                durations_seconds=tuple(tuple(row) for row in durations),
            )

    roads = MeasuredRoads()
    result = TripPlanner(roads, FakeCandidates(), roads).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal(3),
        optimization_mode=mode,
        matrix_first=True,
        budget=budget,
    )
    assert result.fuel.exact_purchase_cost == expected_cost
    assert roads.calls == directions
    assert ticks[0] == elapsed
    assert result.outbound_attempts <= 4
    assert result.verified.duration_seconds <= result.baseline.duration_seconds + 1800
    assert result.fuel.ending_gallons >= 0
    assert all(visit.departure_gallons <= 50 for visit in result.fuel.visits)
    assert budget.planning_remaining_seconds == 13.5 - elapsed


def test_child_branches_cannot_change_request_optimization_policy():
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    budget.configure_mode("lowest_fuel_cost")
    budget.reserve_finalization(1.5)
    child = budget.child(14)
    ticks[0] = 10
    child.configure_mode("lowest_fuel_cost")
    assert child.target_remaining_seconds == 3.5
    assert child.planning_remaining_seconds == 3.5
    with pytest.raises(ValueError, match="share optimization mode"):
        child.configure_mode("balanced")
