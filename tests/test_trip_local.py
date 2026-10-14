from decimal import Decimal

from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.trip import TripPlanner
from tests.support.planning import (
    FINISH,
    START,
    STATIONS,
    FakeCandidates,
    FakeRouter,
    _route,
)


def test_local_first_uses_completed_graph_and_same_router_for_verification() -> None:
    class EmptyCandidates(FakeCandidates):
        def along_route(self, route, corridor_meters=8000):
            return ()

    class LocalRouter(FakeRouter):
        def route(self, points, budget):
            budget.check_deadline()
            self.calls += 1
            if len(points) == 2:
                return _route(points, (1000,))
            return _route(points, (400, 400, 200))

    class CompletedGraph:
        def propose(self, *args, **kwargs):
            assert args[4] is None
            return tuple(
                ProjectedCandidate(
                    station,
                    Decimal(progress) * METERS_PER_MILE,
                    Decimal(0),
                    "completed-road",
                )
                for station, progress in zip(STATIONS, (400, 800), strict=True)
            )

    router = LocalRouter()
    result = TripPlanner(
        router, EmptyCandidates(), national_refiner=CompletedGraph()
    ).plan_local_first(
        START, FINISH, Decimal(50), Decimal(0), None, None, Decimal("3.20")
    )
    assert result.selection_method == "csv_road_first"
    assert tuple(item.station_id for item in result.routed_stations) == ("1", "2")
    assert result.outbound_attempts == 0
    assert router.calls == 2


def test_local_first_keeps_verified_corridor_when_graph_has_no_improvement() -> None:
    class LocalRouter(FakeRouter):
        def route(self, points, budget):
            budget.check_deadline()
            self.calls += 1
            return _route(points, (1000,) if len(points) == 2 else (400, 400, 200))

    class CompletedGraph:
        def propose(self, *args, **kwargs):
            assert args[4] is not None
            return None

    result = TripPlanner(
        LocalRouter(), FakeCandidates(), national_refiner=CompletedGraph()
    ).plan_local_first(
        START, FINISH, Decimal(50), Decimal(0), None, None, Decimal("3.20")
    )
    assert result.selection_method == "local_road_corridor_first"
    assert tuple(item.station_id for item in result.routed_stations) == ("1", "2")


def test_local_verified_cost_and_distance_tie_adopts_fewer_waypoints() -> None:
    class LocalRouter(FakeRouter):
        def route(self, points, budget):
            budget.check_deadline()
            miles = {2: (1000,), 3: (400, 500), 4: (400, 300, 200)}
            return _route(points, miles[len(points)])

    class CompletedGraph:
        def propose(self, *args, **kwargs):
            assert args[4] == 120
            return (
                ProjectedCandidate(
                    STATIONS[0], Decimal(400) * METERS_PER_MILE, Decimal(0), "road"
                ),
            )

    result = TripPlanner(
        LocalRouter(), FakeCandidates(), national_refiner=CompletedGraph()
    ).plan_local_first(START, FINISH, Decimal(50), Decimal(0), None, None, Decimal(3))
    assert result.fuel.exact_purchase_cost == 120
    assert result.verified.distance_meters == 900 * METERS_PER_MILE
    assert tuple(station.station_id for station in result.routed_stations) == ("1",)


def test_local_first_retains_verified_corridor_after_refiner_phase_deadline() -> None:
    ticks = [0.0]

    class LocalRouter(FakeRouter):
        def route(self, points, budget):
            budget.check_deadline()
            return _route(points, (1000,) if len(points) == 2 else (400, 400, 200))

    class SlowGraph:
        def propose(self, *args, **kwargs):
            phase_budget = args[-1]
            ticks[0] = 8.0
            phase_budget.check_deadline()
            return None

    result = TripPlanner(
        LocalRouter(), FakeCandidates(), national_refiner=SlowGraph()
    ).plan_local_first(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal("3.20"),
        budget=RequestBudget(clock=lambda: ticks[0]),
    )
    assert result.selection_method == "local_road_corridor_first"
    assert result.outbound_attempts == 0


def test_local_completed_rows_can_recover_a_missing_hosted_fuel_chain() -> None:
    class EmptyCandidates(FakeCandidates):
        def along_route(self, route, corridor_meters=8000):
            return ()

    class Recovery:
        allow_partial = True

        def __init__(self) -> None:
            self.calls = []

        def propose(
            self,
            start,
            finish,
            initial,
            reserve,
            incumbent_cost,
            max_total_seconds,
            max_total_meters,
            budget,
            **kwargs,
        ):
            self.calls.append(incumbent_cost)
            if incumbent_cost is not None:
                return None
            return tuple(
                ProjectedCandidate(
                    station,
                    Decimal(progress) * METERS_PER_MILE,
                    Decimal(0),
                    "local-recovery",
                )
                for station, progress in zip(STATIONS, (400, 800), strict=True)
            )

    recovery = Recovery()
    router = FakeRouter()
    trip = TripPlanner(router, EmptyCandidates(), national_refiner=recovery).plan(
        START, FINISH, Decimal(50), Decimal(0), None, None, Decimal("3.20")
    )
    assert trip.selection_method == "csv_road_recovery"
    assert tuple(station.station_id for station in trip.routed_stations) == ("1", "2")
    assert recovery.calls[0] is None
    assert router.calls == 2
