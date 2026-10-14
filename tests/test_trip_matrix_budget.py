from decimal import Decimal

from app.domain.road import RoadDistanceMatrix
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.trip import TripPlanner
from tests.support.planning import (
    FINISH,
    START,
    FakeCandidates,
    FakeMatrixRouter,
    _route,
)


def test_matrix_time_allowance_is_relative_to_its_own_baseline() -> None:
    class SlowerProfileRouter(FakeMatrixRouter):
        def matrix(self, points, budget):
            source = super().matrix(points, budget)
            return RoadDistanceMatrix(
                source.distances_meters,
                source.source_snap_meters,
                source.destination_snap_meters,
                (
                    (Decimal(0), Decimal(680), None, Decimal(2000)),
                    (None, Decimal(0), Decimal(680), None),
                    (None, None, Decimal(0), Decimal(680)),
                    (None, None, None, Decimal(0)),
                ),
            )

        def route(self, points, budget):
            budget.consume_attempt()
            self.calls += 1
            if len(points) == 2:
                return _route(points, (1000,))
            return _route(points, (400, 400, 200), seconds_per_leg=50)

    router = SlowerProfileRouter()
    trip = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        "balanced",
        Decimal(1),
    )
    assert trip.selection_method == "bounded_directed_matrix_graph"
    assert trip.verified.duration_seconds == 150
    assert trip.outbound_attempts == 3


def test_opt_in_matrix_graph_uses_three_shared_attempts() -> None:
    router = FakeMatrixRouter()
    trip = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
    )
    assert trip.selection_method == "bounded_directed_matrix_graph"
    assert [station.station_id for station in trip.routed_stations] == ["1", "2"]
    assert trip.outbound_attempts == 3
    assert router.calls == 2


def test_graph_quality_search_uses_hard_budget_with_verification_reserve(
    monkeypatch,
) -> None:
    from app.planning.graph_search import search_fuel_graph
    from app.planning.verification_timing import VerificationTiming

    monkeypatch.setattr(
        "app.planning.matrix_selection.verification_timing", VerificationTiming()
    )

    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    observations = []

    def observed_search(*args, **kwargs):
        for elapsed in (10.0, 10.999, 11.0):
            ticks[0] = elapsed
            observations.append(kwargs["stop_when"]())
        return search_fuel_graph(*args, **kwargs)

    monkeypatch.setattr(
        "app.planning.matrix_selection.search_fuel_graph", observed_search
    )
    router = FakeMatrixRouter()
    trip = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
        budget=budget,
    )
    assert observations == [False, False, True]
    assert trip.verified
    assert trip.outbound_attempts <= 4


def test_verified_checkpoint_limits_improvement_to_target_with_verification_reserve(
    monkeypatch,
) -> None:
    from app.planning.graph_search import search_fuel_graph
    from app.planning.verification_timing import VerificationTiming

    monkeypatch.setattr(
        "app.planning.matrix_selection.verification_timing", VerificationTiming()
    )
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    budget.publish_verified_payload(
        {"status": "verified"}, (Decimal(100), Decimal(10), 0, ())
    )
    observations = []

    def observed_search(*args, **kwargs):
        for elapsed in (5.999, 6.0):
            ticks[0] = elapsed
            observations.append(kwargs["stop_when"]())
        return search_fuel_graph(*args, **kwargs)

    monkeypatch.setattr(
        "app.planning.matrix_selection.search_fuel_graph", observed_search
    )
    trip = TripPlanner(
        FakeMatrixRouter(), FakeCandidates(), matrix_provider=FakeMatrixRouter()
    ).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
        budget=budget,
    )
    assert observations == [False, True]
    assert trip.verified


def test_graph_proposal_leaves_preferred_window_for_first_road_verification(
    monkeypatch,
) -> None:
    from dataclasses import replace

    from app.planning.graph_search import search_fuel_graph
    from app.planning.verification_timing import VerificationTiming

    monkeypatch.setattr(
        "app.planning.matrix_selection.verification_timing", VerificationTiming()
    )
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    observations = []

    def observed_search(*args, **kwargs):
        stop = kwargs.pop("stop_when")
        result = search_fuel_graph(*args, **kwargs)
        assert budget.first_verified_seconds is None
        assert result.solution is not None
        for elapsed in (5.999, 6.0):
            ticks[0] = elapsed
            observations.append(stop())
        return replace(result, complete=False)

    monkeypatch.setattr(
        "app.planning.matrix_selection.search_fuel_graph", observed_search
    )
    router = FakeMatrixRouter()
    result = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        budget=budget,
    )
    assert observations == [False, True]
    assert result.verified and result.fuel.exact_purchase_cost >= 0
    assert result.outbound_attempts <= 4


def test_matrix_first_verifies_direct_baseline_and_keeps_repair_slot() -> None:
    router = FakeMatrixRouter()
    trip = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        matrix_first=True,
    )
    assert trip.selection_method == "road_first_directed_graph"
    assert [station.station_id for station in trip.routed_stations] == ["1", "2"]
    assert trip.outbound_attempts == 3
    assert router.calls == 2


def test_matrix_first_no_stop_uses_directions_time_as_policy_baseline() -> None:
    class DivergentDirectRouter(FakeMatrixRouter):
        def matrix(self, points, budget):
            original = super().matrix(points, budget)
            distances = tuple(
                tuple(
                    Decimal(450) * METERS_PER_MILE if row == 0 and col == 3 else value
                    for col, value in enumerate(values)
                )
                for row, values in enumerate(original.distances_meters)
            )
            return RoadDistanceMatrix(
                distances,
                original.source_snap_meters,
                original.destination_snap_meters,
                original.durations_seconds,
            )

        def route(self, points, budget):
            budget.consume_attempt()
            self.calls += 1
            return _route(points, (450,), seconds_per_leg=2000)

    router = DivergentDirectRouter()
    trip = TripPlanner(router, FakeCandidates(), matrix_provider=router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        matrix_first=True,
    )
    assert trip.selection_method == "road_first_no_stop"
    assert trip.baseline.duration_seconds == Decimal(2000)
    assert trip.verified.duration_seconds == Decimal(2000)
    assert trip.outbound_attempts == 1
