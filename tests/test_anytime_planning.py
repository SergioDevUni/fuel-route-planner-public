"""Verified checkpoints and exact dominance survive interrupted improvement."""

import random
from decimal import Decimal
from threading import Event
from time import monotonic

import pytest

from app.api.deadline import run_with_deadline
from app.domain.road import RoadDistanceMatrix
from app.domain.units import METERS_PER_MILE
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.fuel_labels import FuelLabel, LabelFrontier
from app.planning.fuel_profile import FuelCostProfile
from app.planning.portfolio import choose_portfolio_plan
from app.planning.trip import TripPlanner
from app.planning.verification_timing import VerificationTiming
from app.routing.client import RoutingUnavailable
from tests.support.planning import (
    FINISH,
    START,
    FakeCandidates,
    FakeRouter,
    _station,
)
from tests.support.portfolio import _plan


def test_deadline_returns_only_a_completed_snapshot_and_stops_late_publishing():
    release, published, finished = Event(), Event(), Event()
    budget = RequestBudget()
    payload = {"status": "verified", "costs": {"fuel_purchases_cost": "100"}}
    rank = (Decimal(100), Decimal(10), 1, ("one",))

    def work():
        try:
            budget.publish_verified_payload(payload, rank)
            published.set()
            release.wait(2)
            budget.publish_verified_payload(payload, rank)
            return {"status": "pending_check proposal"}
        finally:
            finished.set()

    def fallback():
        result = budget.verified_payload()
        budget.stop_work()
        return result

    try:
        result = run_with_deadline(work, 0.05, on_timeout=fallback)
        assert published.is_set()
        assert result == payload
        result["costs"]["fuel_purchases_cost"] = "0"
        assert budget.verified_payload() == payload
    finally:
        release.set()
        assert finished.wait(1)


def test_expired_or_pending_check_work_has_no_successful_fallback():
    budget = RequestBudget()
    with pytest.raises(PlanningDeadlineExceeded):
        run_with_deadline(
            lambda: (_ for _ in ()).throw(PlanningDeadlineExceeded("late")),
            1,
            on_timeout=budget.verified_payload,
        )
    assert budget.verified_payload() is None


def test_checkpoint_profiles_are_not_ranked_by_incomparable_prices():
    budget = RequestBudget()
    assert budget.verified_routing_profile is None
    budget.child(5).publish_verified_payload(
        {"profile": "local"}, (Decimal(1), Decimal(10), 0, ()), "local"
    )
    assert budget.verified_routing_profile == "local"
    budget.publish_verified_payload(
        {"profile": "hosted"}, (Decimal(100), Decimal(10), 0, ()), "hosted"
    )
    budget.child(5).publish_verified_payload(
        {"profile": "local"}, (Decimal(1), Decimal(10), 0, ()), "local"
    )
    assert budget.verified_payload() == {"profile": "hosted"}
    assert budget.verified_routing_profile == "hosted"
    budget.publish_verified_payload(
        {"profile": "cheaper hosted"}, (Decimal(90), Decimal(10), 0, ()), "hosted"
    )
    assert budget.verified_payload() == {"profile": "cheaper hosted"}


def test_portfolio_retains_ready_local_plan_without_awaiting_hosted_deadline():
    release, finished = Event(), Event()
    local = _plan("2", "local")

    def slow_hosted(_):
        try:
            release.wait(2)
            raise PlanningDeadlineExceeded("late hosted proposal")
        finally:
            finished.set()

    began = monotonic()
    try:
        actual = choose_portfolio_plan(
            slow_hosted,
            lambda _: local,
            lambda *args: None,
            RequestBudget(deadline_seconds=2.1),
        )
        assert actual.selection_method == "local"
        assert monotonic() - began < 0.5
    finally:
        release.set()
        assert finished.wait(1)


def test_curve_merge_preserves_the_original_full_domain_predicate():
    rng = random.Random(71443)
    profiles = []
    for _ in range(100):
        q = Decimal(rng.randint(0, 25)) / 2
        profile = FuelCostProfile.initial(q)
        for _ in range(5):
            profile = profile.buy(Decimal(rng.randint(1, 60)) / 10, Decimal(25))
            profile = profile.drive(Decimal(rng.randint(0, 15)) / 2)
            assert profile is not None
        profiles.append(profile)
    for left in profiles:
        for right in rng.sample(profiles, 10):
            points = {right.minimum_fuel, right.maximum_fuel}
            points.update(
                q
                for q, _ in left.points + right.points
                if right.minimum_fuel < q < right.maximum_fuel
            )
            expected = left.maximum_fuel >= right.maximum_fuel and all(
                left.cost_at(max(q, left.minimum_fuel)) <= right.cost_at(q)
                for q in points
            )
            assert left.covers(right) == expected


def test_visited_time_index_matches_exhaustive_frontier_comparisons():
    rng = random.Random(930158)
    frontier, expected = LabelFrontier(), []
    for serial in range(500):
        mask = rng.randint(0, 255)
        nodes = tuple(i for i in range(8) if mask & (1 << i))
        label = FuelLabel(
            1,
            nodes,
            (),
            Decimal(rng.randint(0, 200)),
            Decimal(rng.randint(0, 100)),
            FuelCostProfile(
                (
                    (Decimal(0), Decimal(rng.randint(0, 80))),
                    (Decimal(10), Decimal(rng.randint(80, 150))),
                )
            ),
            mask,
            tuple(str(i) for i in nodes),
        )
        accepted = not any(other.dominates(label) for other in expected)
        if accepted:
            expected = [other for other in expected if not label.dominates(other)]
            expected.append(label)
        actual, _ = frontier.accept(label)
        assert actual == accepted, serial
        query = FuelLabel(
            1,
            (),
            (),
            Decimal(0),
            Decimal(0),
            FuelCostProfile.initial(Decimal(0)),
            0,
            (),
        )
        assert {id(v) for v in frontier.eligible(query, subsets=False)} == {
            id(v) for v in expected
        }


def test_verification_reserve_uses_profile_and_waypoint_size_without_route_ids():
    timing = VerificationTiming()
    assert timing.reserve("hosted", 12) == 4
    for _ in range(8):
        timing.observe("hosted", 12, 4.5)
        timing.observe("local", 12, 0.1)
    assert timing.reserve("hosted", 12) == 5.25
    assert timing.reserve("local", 12) == 3
    assert timing.reserve("hosted", 30) == 4


def test_cheaper_pending_check_proposal_cannot_replace_verified_incumbent():
    cheap = _station("cheap", -83, "1")
    checkpoints = []

    class Candidates(FakeCandidates):
        def within_detour_envelope(self, route, max_total_meters):
            return (
                ProjectedCandidate(
                    cheap, Decimal(350) * METERS_PER_MILE, Decimal(50), "cheap"
                ),
            )

    class RoutingPort(FakeRouter):
        def matrix(self, points, budget):
            assert checkpoints, "initial route must be verified before improvement"
            budget.consume_attempt()
            positions = {-90: 0, -83: 350, -82: 400, -74: 800, -70: 1000}
            markers = [positions[p.longitude] for p in points]
            distances = tuple(
                tuple(
                    Decimal(b - a) * METERS_PER_MILE if b >= a else None
                    for b in markers
                )
                for a in markers
            )
            durations = tuple(
                tuple(
                    Decimal(0) if a == b else Decimal(100) if b > a else None
                    for b in markers
                )
                for a in markers
            )
            return RoadDistanceMatrix(
                distances,
                (Decimal(0),) * len(points),
                (Decimal(0),) * len(points),
                durations,
            )

        def route(self, points, budget):
            if len(points) > 2 and any(p.longitude == -83 for p in points):
                budget.consume_attempt()
                raise RoutingUnavailable("optional cheaper verification unavailable")
            return super().route(points, budget)

    router = RoutingPort()
    trip = TripPlanner(
        router, Candidates(), router, on_verified=checkpoints.append
    ).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal(3),
        matrix_first=True,
    )
    assert checkpoints[0].fuel.exact_purchase_cost == Decimal(160)
    assert trip.fuel.exact_purchase_cost == Decimal(160)
    assert tuple(s.station_id for s in trip.routed_stations) == ("1", "2")
    assert trip.outbound_attempts == 4
    assert trip.selection_method == "road_first_verified_incumbent"


def test_stage_timings_include_inflight_work_and_checkpoint_cache_is_isolated():
    from app.api.cache import VerifiedIncumbentCache

    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    with budget.stage("search"):
        ticks[0] = 2
        assert budget.stage_seconds["search"] == 2
    assert budget.stage_seconds["search"] == 2
    cache = VerifiedIncumbentCache()
    rank = (Decimal(10), Decimal(1), 0, ())
    cache.put("same valid inputs", "hosted", rank, {"cost": "10"})
    cache.put(
        "same valid inputs", "hosted", (Decimal(20), Decimal(1), 0, ()), {"cost": "20"}
    )
    copied = cache.get("same valid inputs")
    assert copied["hosted"][1] == {"cost": "10"}
    copied["hosted"][1]["cost"] = "0"
    assert cache.get("same valid inputs")["hosted"][1] == {"cost": "10"}
    assert cache.get("changed CSV or constraints") == {}
