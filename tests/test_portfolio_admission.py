"""Speculative CPU work yields to competing primary requests."""

from concurrent.futures import wait as real_wait
from threading import BoundedSemaphore, Event

from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.portfolio import choose_portfolio_plan, primary_request_work
from tests.support.portfolio import _plan


def test_loaded_portfolio_retains_primary_and_skips_speculative_work():
    budget, other = RequestBudget(), RequestBudget()
    called = Event()
    hosted = _plan("3", "hosted")

    def local(_):
        called.set()
        return _plan("2", "local")

    with primary_request_work(other):
        result = choose_portfolio_plan(
            lambda _: hosted, local, lambda *args: None, budget
        )
    assert result.selection_method == "hosted"
    assert not called.is_set()
    assert budget.metrics["portfolio_speculation_skipped"] == 1
    assert not budget.cancelled


def test_running_speculative_branch_yields_without_cancelling_primary():
    started, pressure_ready, finished = Event(), Event(), Event()
    budget = RequestBudget()
    hosted = _plan("3", "hosted")

    def primary(_):
        assert started.wait(1)
        with primary_request_work(RequestBudget()):
            pressure_ready.set()
            assert finished.wait(1)
        return hosted

    def local(child):
        started.set()
        assert pressure_ready.wait(1)
        try:
            child.check_deadline()
        except PlanningDeadlineExceeded:
            finished.set()
            raise
        raise AssertionError("speculative work ignored primary pressure")

    result = choose_portfolio_plan(primary, local, lambda *args: None, budget)
    assert result.selection_method == "hosted"
    budget.check_deadline()


def test_no_comparison_reserve_cuts_off_primary_with_all_calls_used(monkeypatch):
    ticks = [0.0]
    budget = RequestBudget(clock=lambda: ticks[0])
    for _ in range(4):
        budget.consume_attempt()
    release = Event()
    seed, cheaper = _plan("3", "seed"), _plan("2", "completed-primary")

    def primary(_):
        assert release.wait(1)
        return cheaper

    def observed_wait(pending, timeout, return_when):
        if ticks[0] == 0:
            ticks[0] = 13.25
            return set(), pending
        release.set()
        return real_wait(pending, timeout=1, return_when=return_when)

    monkeypatch.setattr("app.planning.portfolio.wait", observed_wait)
    monkeypatch.setattr(
        "app.planning.portfolio._speculative_slots", BoundedSemaphore(0)
    )
    try:
        result = choose_portfolio_plan(
            primary,
            lambda _: seed,
            lambda *args: None,
            budget,
            hosted_incumbent=lambda: seed,
        )
        assert result.selection_method == "completed-primary"
    finally:
        release.set()
