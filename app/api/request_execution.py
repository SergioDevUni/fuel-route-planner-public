"""Request coordination, verified-plan recovery and response finalization."""

from collections.abc import Callable
from typing import Any

# Django does not ship typing metadata; keep this exception at the adapter imports.
from django.db import close_old_connections  # type: ignore[import-untyped]
from django.http import HttpResponse  # type: ignore[import-untyped]

from app.api.cache import ResultCache
from app.api.coalescing import RequestCoalescer
from app.api.validation import TripInput
from app.planning.budget import RequestBudget
from app.planning.portfolio import primary_request_work


def execute_request(
    trip: TripInput,
    budget: RequestBudget,
    *,
    calculation_keys: Callable[[TripInput, RequestBudget], tuple[str, str, str] | None],
    trip_cache: ResultCache,
    calculate: Callable[[TripInput, RequestBudget], dict[str, Any]],
    request_coalescer: RequestCoalescer,
    run_with_deadline: Callable[..., dict[str, Any]],
    save_response: Callable[[dict[str, Any], RequestBudget], HttpResponse],
) -> HttpResponse:
    budget.configure_mode(trip.optimization_mode)
    keys = calculation_keys(trip, budget)
    if keys is not None:
        payload = trip_cache.get_completed(keys[0])
        if payload is not None:
            budget.increment_metric("completed_result_reuses", 1)
            budget.stop_work()
            return save_response(payload, budget)
    submitted = budget.clock()

    def plan() -> dict[str, Any]:
        try:
            budget.increment_metric(
                "planning_worker_queue_seconds", budget.clock() - submitted
            )
            with primary_request_work(budget):
                return calculate(trip, budget)
        finally:
            close_old_connections()

    def verified_fallback(*, stop_without_result: bool = True) -> dict[str, Any] | None:
        payload = budget.verified_payload()
        if payload is not None or stop_without_result:
            budget.stop_work()
        if payload is not None:
            budget.increment_metric("verified_plan_recoveries", 1)
            budget.cache_completed_payload(payload)
        return payload

    reserve = min(1.5, budget.remaining_seconds / 10)
    budget.reserve_finalization(reserve)

    def admitted_plan() -> dict[str, Any]:
        if keys is not None:
            completed = trip_cache.get_completed(keys[0])
            if completed is not None:
                budget.increment_metric("completed_result_reuses", 1)
                return completed
        return run_with_deadline(
            plan,
            budget.planning_remaining_seconds,
            on_timeout=verified_fallback,
            on_shutdown=budget.stop_work,
            preferred_seconds=max(0.001, budget.target_remaining_seconds),
            on_preferred=lambda: verified_fallback(stop_without_result=False),
            admission_wait_seconds=min(
                budget.settings.admission_wait_seconds,
                budget.target_remaining_seconds,
            ),
            on_admission=lambda seconds: budget.increment_metric(
                "planning_admission_wait_seconds", seconds
            ),
        )

    payload = (
        request_coalescer.run(keys[0], admitted_plan, budget)
        if keys is not None
        else admitted_plan()
    )
    budget.stop_work()
    return save_response(payload, budget)
