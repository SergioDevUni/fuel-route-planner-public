from decimal import Decimal

import pytest

from app.planning.budget import RequestBudget
from app.planning.trip import TripPlanner
from app.planning.verification_timing import VerificationTiming
from tests.support.planning import FINISH, START, FakeCandidates, FakeMatrixRouter


def test_response_cutoffs_are_absolute_and_shared_with_child_phases():
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    child = budget.child(14)
    ticks[0] = 1
    budget.reserve_finalization(1.5)
    assert child.target_remaining_seconds == 7.5
    assert child.planning_remaining_seconds == 12.5
    assert budget.remaining_seconds == 14
    ticks[0] = 9
    assert budget.target_remaining_seconds == 0
    assert child.planning_remaining_seconds == 4.5
    assert child.child(1).planning_remaining_seconds == 1
    budget.check_deadline()  # Recovery is still allowed after the preferred cutoff.
    with pytest.raises(ValueError):
        budget.reserve_finalization(7)


def test_slow_matrix_keeps_incumbent_without_starting_late_verification(monkeypatch):
    monkeypatch.setattr(
        "app.planning.matrix_selection.verification_timing", VerificationTiming()
    )
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    budget.reserve_finalization(1.5)

    class SlowRoads(FakeMatrixRouter):
        def route(self, points, budget):
            route = super().route(points, budget)
            ticks[0] += 2
            return route

        def matrix(self, points, budget):
            result = super().matrix(points, budget)
            ticks[0] += 2.5
            return result

    roads = SlowRoads()
    plan = TripPlanner(roads, FakeCandidates(), roads).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal(3),
        matrix_first=True,
        budget=budget,
    )
    assert ticks[0] == 6.5
    assert plan.fuel.exact_purchase_cost == 160
    assert roads.calls == 2
    assert budget.attempts == 3
    assert budget.metrics["optional_verification_skipped_for_deadline"] == 1


def test_search_without_snapshot_uses_recovery_cutoff_with_finalization(monkeypatch):
    from app.planning.graph_search import search_fuel_graph

    monkeypatch.setattr(
        "app.planning.matrix_selection.verification_timing", VerificationTiming()
    )
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    budget.reserve_finalization(1.5)
    observations = []

    def search(*args, **kwargs):
        for elapsed in (8.5, 9.499, 9.5):
            ticks[0] = elapsed
            observations.append(kwargs["stop_when"]())
        return search_fuel_graph(*args, **kwargs)

    monkeypatch.setattr("app.planning.matrix_selection.search_fuel_graph", search)
    roads = FakeMatrixRouter()
    plan = TripPlanner(roads, FakeCandidates(), roads).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal(3),
        budget=budget,
    )
    assert observations == [False, False, True]
    assert plan.verified


@pytest.mark.parametrize(
    ("mode", "preferred"), [("balanced", 8.5), ("lowest_fuel_cost", 13.5)]
)
def test_api_passes_the_same_budget_window_to_its_worker(mode, preferred):
    from app.api.cache import ResultCache
    from app.api.coalescing import RequestCoalescer
    from app.api.request_execution import execute_request
    from app.api.validation import TripInput

    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    trip = TripInput(
        {}, {}, Decimal(50), None, Decimal(0), None, None, optimization_mode=mode
    )
    observed = {}

    def runner(work, seconds, **options):
        observed.update(seconds=seconds, **options)
        assert options["preferred_seconds"] == budget.target_remaining_seconds
        assert seconds == budget.planning_remaining_seconds
        return {"ok": True}

    response = execute_request(
        trip,
        budget,
        calculation_keys=lambda *_: None,
        trip_cache=ResultCache(),
        calculate=lambda *_: {},
        request_coalescer=RequestCoalescer(),
        run_with_deadline=runner,
        save_response=lambda payload, _: payload,
    )
    assert response == {"ok": True}
    assert observed["seconds"] == 13.5
    assert observed["preferred_seconds"] == preferred
