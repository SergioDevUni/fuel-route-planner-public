"""Concurrent plans share policy counters and compare verified purchase costs."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import BoundedSemaphore, Event
from time import monotonic

import pytest

from app.planning.budget import (
    PlanningDeadlineExceeded,
    RequestBudget,
    RoutingBudgetExceeded,
)
from app.planning.portfolio import choose_portfolio_plan, needs_local_expansion
from app.planning.trip_types import DurationLimitExceeded, NoFeasiblePlan, PlannedTrip
from tests.support.portfolio import _plan


def test_child_budgets_share_atomic_four_call_ceiling() -> None:
    budget = RequestBudget()
    children = [budget.child(10) for _ in range(20)]

    def attempt(child: RequestBudget) -> bool:
        try:
            child.consume_attempt()
        except RoutingBudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=20) as executor:
        outcomes = list(executor.map(attempt, children))
    assert sum(outcomes) == budget.attempts == 4


def test_child_deadline_does_not_extend_parent() -> None:
    ticks = [0.0]
    budget = RequestBudget(deadline_seconds=15, clock=lambda: ticks[0])
    ticks[0] = 5.0
    child = budget.child(20)
    assert child.remaining_seconds == budget.remaining_seconds == 10
    ticks[0] = 15.0
    assert child.remaining_seconds == 0


def test_child_soft_target_does_not_restart_online_clock() -> None:
    ticks = [0.0]
    parent = RequestBudget(clock=lambda: ticks[0])
    ticks[0] = 6.0
    child = parent.child(12)
    assert child.remaining_seconds == 9
    assert child.target_remaining_seconds == parent.target_remaining_seconds == 4
    ticks[0] = 10.0
    assert child.target_remaining_seconds == 0
    assert child.remaining_seconds == 5
    child.check_deadline()


def test_verified_hosted_plan_retains_hard_deadline_after_soft_target() -> None:
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    expected = _plan("3", "hosted")

    def hosted(shared: RequestBudget) -> PlannedTrip:
        shared.consume_attempt()
        ticks[0] = 13.0
        assert shared.target_remaining_seconds == 0
        shared.check_deadline()
        return expected

    def unavailable(_: RequestBudget) -> PlannedTrip:
        raise NoFeasiblePlan("no local incumbent")

    actual = choose_portfolio_plan(
        hosted, unavailable, lambda one, two, shared: None, budget
    )
    assert actual.selection_method == "hosted"
    assert actual.outbound_attempts == 1
    assert budget.remaining_seconds == 2.0


def test_complete_bounded_matrix_still_needs_purchase_quality_expansion() -> None:
    plan = replace(_plan("3", "road_first_directed_graph"), matrix_search_complete=True)
    assert needs_local_expansion(plan)


def test_default_planning_deadline_is_fifteen_seconds() -> None:
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    ticks[0] = 14.999
    budget.check_deadline()
    ticks[0] = 15.0
    with pytest.raises(PlanningDeadlineExceeded):
        budget.check_deadline()


def test_portfolio_chooses_cheaper_common_profile_plan() -> None:
    budget = RequestBudget()
    hosted = _plan("4", "hosted")
    local = _plan("2", "local", "csv-2")
    compared = replace(local, selection_method="common-profile")

    def hosted_work(child: RequestBudget) -> PlannedTrip:
        child.consume_attempt()
        return hosted

    def local_work(child: RequestBudget) -> PlannedTrip:
        child.check_deadline()
        return local

    def compare(
        one: PlannedTrip, two: PlannedTrip, shared: RequestBudget
    ) -> PlannedTrip:
        assert one is hosted and two is local
        shared.consume_attempt()
        return compared

    chosen = choose_portfolio_plan(hosted_work, local_work, compare, budget)
    assert chosen.selection_method == "common-profile"
    assert chosen.outbound_attempts == budget.attempts == 2


def test_portfolio_keeps_hosted_when_local_comparison_fails() -> None:
    hosted = _plan("3", "hosted")
    local = _plan("1", "local")
    result = choose_portfolio_plan(
        lambda child: hosted,
        lambda child: local,
        lambda one, two, budget: None,
        RequestBudget(),
    )
    assert result.selection_method == "hosted"


def test_portfolio_skips_comparison_for_same_csv_stop_sequence() -> None:
    hosted = _plan("3", "hosted")
    local = _plan("3", "local")

    def unexpected_compare(
        one: PlannedTrip, two: PlannedTrip, shared: RequestBudget
    ) -> PlannedTrip:
        raise AssertionError("same stop sequence needs no extra hosted call")

    result = choose_portfolio_plan(
        lambda child: hosted,
        lambda child: local,
        unexpected_compare,
        RequestBudget(),
    )
    assert result.selection_method == "hosted"


def test_portfolio_can_return_verified_local_when_hosted_fails() -> None:
    local = _plan("2", "local")

    def failed(_: RequestBudget) -> PlannedTrip:
        raise ValueError("hosted unavailable")

    result = choose_portfolio_plan(
        failed, lambda child: local, lambda one, two, budget: None, RequestBudget()
    )
    assert result.selection_method == "local"


def test_portfolio_retries_local_once_when_both_initial_paths_fail() -> None:
    local = _plan("2", "local")
    calls = 0

    def failed_hosted(_: RequestBudget) -> PlannedTrip:
        raise ValueError("hosted unavailable")

    def intermittent_local(_: RequestBudget) -> PlannedTrip:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NoFeasiblePlan("partial road scope changed")
        return local

    result = choose_portfolio_plan(
        failed_hosted,
        intermittent_local,
        lambda one, two, budget: None,
        RequestBudget(),
    )
    assert result is not None and result.selection_method == "local"
    assert calls == 2


def test_portfolio_raises_when_neither_candidate_verifies() -> None:
    def failed(_: RequestBudget) -> PlannedTrip:
        raise ValueError("no route")

    with pytest.raises(ValueError, match="no route"):
        choose_portfolio_plan(
            failed, failed, lambda one, two, budget: None, RequestBudget()
        )


def test_portfolio_reports_local_timeout_when_hosted_chain_exceeds_time_cap() -> None:
    def hosted(_: RequestBudget) -> PlannedTrip:
        raise DurationLimitExceeded("hosted fallback is too slow")

    def local(_: RequestBudget) -> PlannedTrip:
        raise PlanningDeadlineExceeded("local verification ran out of time")

    with pytest.raises(PlanningDeadlineExceeded, match="local verification"):
        choose_portfolio_plan(
            hosted, local, lambda one, two, budget: None, RequestBudget()
        )


def test_fast_hosted_result_skips_local_search() -> None:
    hosted = replace(
        _plan("3", "road_first_directed_graph", initial="50"),
        matrix_search_complete=True,
    )

    def unexpected_local(_: RequestBudget) -> PlannedTrip:
        raise AssertionError("local search should not start")

    result = choose_portfolio_plan(
        lambda _: hosted,
        unexpected_local,
        lambda one, two, budget: None,
        RequestBudget(),
        hedge_delay_seconds=0.1,
        expand_hosted=needs_local_expansion,
    )
    assert result.selection_method == "road_first_directed_graph"


def test_incomplete_matrix_result_expands_local_search() -> None:
    hosted = replace(
        _plan("3", "matrix_first_directed_graph"), matrix_search_complete=False
    )
    local = _plan("2", "local", "csv-2")
    result = choose_portfolio_plan(
        lambda _: hosted,
        lambda _: local,
        lambda one, two, budget: replace(two, selection_method="compared"),
        RequestBudget(),
        hedge_delay_seconds=0.1,
        expand_hosted=needs_local_expansion,
    )
    assert result.selection_method == "compared"


def test_slow_hosted_result_starts_local_hedge() -> None:
    local_started = Event()
    hosted = _plan("3", "hosted")
    local = _plan("2", "local", "csv-2")

    def slow_hosted(_: RequestBudget) -> PlannedTrip:
        assert local_started.wait(2)
        return hosted

    def local_work(_: RequestBudget) -> PlannedTrip:
        local_started.set()
        return local

    result = choose_portfolio_plan(
        slow_hosted,
        local_work,
        lambda one, two, budget: replace(two, selection_method="compared"),
        RequestBudget(),
        hedge_delay_seconds=0.01,
        expand_hosted=lambda _: False,
    )
    assert result.selection_method == "compared"


def test_fast_hosted_failure_starts_local_search() -> None:
    local = _plan("2", "local")

    def failed(_: RequestBudget) -> PlannedTrip:
        raise NoFeasiblePlan("hosted chain unavailable")

    result = choose_portfolio_plan(
        failed,
        lambda _: local,
        lambda one, two, budget: None,
        RequestBudget(),
        hedge_delay_seconds=0.1,
    )
    assert result.selection_method == "local"


def test_saturated_portfolio_capacity_uses_hosted_path(monkeypatch) -> None:
    monkeypatch.setattr("app.planning.portfolio._portfolio_slots", BoundedSemaphore(0))
    hosted = _plan("3", "hosted")

    def unexpected_local(_: RequestBudget) -> PlannedTrip:
        raise AssertionError("local work must not start when portfolio is full")

    result = choose_portfolio_plan(
        lambda _: hosted,
        unexpected_local,
        lambda one, two, budget: None,
        RequestBudget(),
    )
    assert result.selection_method == "hosted"


def test_portfolio_returns_at_deadline_when_workers_are_still_running() -> None:
    release = Event()
    pending = []

    def blocked(_: RequestBudget) -> PlannedTrip:
        release.wait(5)
        return _plan("3", "late")

    began = monotonic()
    try:
        with pytest.raises(PlanningDeadlineExceeded):
            choose_portfolio_plan(
                blocked,
                blocked,
                lambda one, two, budget: None,
                RequestBudget(deadline_seconds=2.2),
                on_pending=lambda futures: pending.extend(futures),
            )
        assert monotonic() - began < 2.6
        assert pending
    finally:
        release.set()
