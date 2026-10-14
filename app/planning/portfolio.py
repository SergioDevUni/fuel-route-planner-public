"""Bounded concurrent candidate generation with one request-wide budget."""

import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.ranking import rank_plan
from app.planning.trip_types import DurationLimitExceeded, NoFeasiblePlan, PlannedTrip
from app.routing.local_valhalla import LocalValhallaUnavailable

logger = logging.getLogger(__name__)
_portfolio_slots = threading.BoundedSemaphore(8)
_work_guard = threading.Lock()
_primary_requests: dict[int, int] = {}
_speculative_slots = threading.BoundedSemaphore(1)


def _register_primary(budget: RequestBudget) -> Callable[[], None]:
    key = id(budget)
    with _work_guard:
        _primary_requests[key] = _primary_requests.get(key, 0) + 1

    def release() -> None:
        with _work_guard:
            remaining = _primary_requests[key] - 1
            if remaining:
                _primary_requests[key] = remaining
            else:
                del _primary_requests[key]

    return release


@contextmanager
def primary_request_work(budget: RequestBudget) -> Iterator[None]:
    release = _register_primary(budget)
    try:
        yield
    finally:
        release()


def _primary_pressure(budget: RequestBudget) -> bool:
    with _work_guard:
        return any(key != id(budget) for key in _primary_requests)


def _release_slot_after(
    futures: tuple[Future[PlannedTrip], ...], slot: threading.BoundedSemaphore
) -> None:
    pending = {future for future in futures if not future.done()}
    if not pending:
        slot.release()
        return
    guard = threading.Lock()

    def finished(future: Future[PlannedTrip]) -> None:
        with guard:
            pending.discard(future)
            if not pending:
                slot.release()

    for future in tuple(pending):
        future.add_done_callback(finished)


def needs_local_expansion(plan: PlannedTrip) -> bool:
    """A completed bounded matrix does not rule out cheaper omitted offers."""
    return plan.fuel.exact_purchase_cost > 0


def choose_portfolio_plan(
    hosted: Callable[[RequestBudget], PlannedTrip],
    local: Callable[[RequestBudget], PlannedTrip],
    verify_local_on_hosted_profile: Callable[
        [PlannedTrip, PlannedTrip, RequestBudget], PlannedTrip | None
    ],
    budget: RequestBudget,
    *,
    hedge_delay_seconds: float = 0.0,
    expand_hosted: Callable[[PlannedTrip], bool] | None = None,
    on_pending: Callable[[tuple[Future[PlannedTrip], ...]], None] | None = None,
    hosted_incumbent: Callable[[], PlannedTrip | None] | None = None,
    local_incumbent: Callable[[], PlannedTrip | None] | None = None,
) -> PlannedTrip:
    return PortfolioStages(
        hosted=hosted,
        local=local,
        verify_local_on_hosted_profile=verify_local_on_hosted_profile,
        budget=budget,
        hedge_delay_seconds=hedge_delay_seconds,
        expand_hosted=expand_hosted,
        on_pending=on_pending,
        hosted_incumbent=hosted_incumbent,
        local_incumbent=local_incumbent,
    ).run()


@dataclass
class PortfolioStages:
    hosted: Callable[[RequestBudget], PlannedTrip]
    local: Callable[[RequestBudget], PlannedTrip]
    verify_local_on_hosted_profile: Callable[
        [PlannedTrip, PlannedTrip, RequestBudget], PlannedTrip | None
    ]
    budget: RequestBudget
    hedge_delay_seconds: float
    expand_hosted: Callable[[PlannedTrip], bool] | None
    on_pending: Callable[[tuple[Future[PlannedTrip], ...]], None] | None
    hosted_incumbent: Callable[[], PlannedTrip | None] | None
    local_incumbent: Callable[[], PlannedTrip | None] | None
    hosted_budget: RequestBudget = field(init=False)
    local_budget: RequestBudget = field(init=False)
    slot: threading.BoundedSemaphore = field(init=False)
    executor: ThreadPoolExecutor = field(init=False)
    hosted_future: Future[PlannedTrip] | None = field(init=False)
    local_future: Future[PlannedTrip] | None = field(init=False)
    release_primary: Callable[[], None] = field(init=False)
    primary_attached: bool = field(init=False)
    hosted_plan: PlannedTrip | None = field(init=False)
    local_plan: PlannedTrip | None = field(init=False)
    hosted_error: Exception | None = field(init=False)
    local_error: Exception | None = field(init=False)
    speculation_skipped: bool = field(init=False)

    def run(self) -> PlannedTrip:
        """Own admission and branch lifetime, then select a comparable result."""
        self.budget.check_deadline()
        if self.budget.remaining_seconds <= 2:
            raise ValueError("portfolio requires a comparison reserve")
        self.hosted_budget = self.budget
        self.local_budget = self.budget.child(
            max(
                0.1,
                min(
                    6.5,
                    self.budget.target_remaining_seconds - 3.0,
                    self.budget.remaining_seconds - 1.0,
                ),
            )
        )
        if self.hedge_delay_seconds < 0:
            raise ValueError("hedge delay must be nonnegative")
        self.slot = _portfolio_slots
        if not self.slot.acquire(blocking=False):
            logger.info("portfolio_capacity_full_using_hosted")
            plan = self.hosted(self.budget)
            self.budget.check_deadline()
            return replace(plan, outbound_attempts=self.budget.attempts)
        try:
            self.executor = ThreadPoolExecutor(max_workers=2)
        except Exception:
            self.slot.release()
            raise
        self.hosted_future: Future[PlannedTrip] | None = None
        self.local_future: Future[PlannedTrip] | None = None
        self.release_primary = _register_primary(self.budget)
        self.primary_attached = False
        try:
            self._launch_primary()
            fast = self._hedge()
            if fast is not None:
                return fast
            self._launch_speculation()
            self._wait_candidates()
        finally:
            self._close()
        self._retry_local()
        return self._choose()

    def _launch_primary(self) -> None:
        """Register and submit primary work before speculative work."""
        submitted = self.budget.clock()

        def primary() -> PlannedTrip:
            self.budget.increment_metric(
                "hosted_queue_seconds", self.budget.clock() - submitted
            )
            with self.budget.stage("hosted_branch"):
                return self.hosted(self.hosted_budget)

        self.hosted_future = self.executor.submit(primary)
        self.hosted_future.add_done_callback(lambda _: self.release_primary())
        self.primary_attached = True
        self.hosted_plan: PlannedTrip | None = None
        self.local_plan: PlannedTrip | None = None
        self.hosted_error: Exception | None = None
        self.local_error: Exception | None = None

    def _hedge(self) -> PlannedTrip | None:
        """Wait for a fast primary result before hedging."""
        assert self.hosted_future is not None
        if self.hedge_delay_seconds:
            try:
                self.hosted_plan = self.hosted_future.result(
                    timeout=min(self.hedge_delay_seconds, self.budget.remaining_seconds)
                )
            except FutureTimeout:
                pass
            except Exception as error:
                self.hosted_error = error
                logger.info("portfolio_hosted_failed reason=%s", type(error).__name__)
            if (
                self.hosted_future.done()
                and self.hosted_plan is not None
                and (
                    not self.hosted_plan.routed_stations
                    or (
                        self.expand_hosted is not None
                        and (not self.expand_hosted(self.hosted_plan))
                    )
                )
            ):
                logger.info("portfolio_hosted_fast_path")
                self.budget.check_deadline()
                return replace(self.hosted_plan, outbound_attempts=self.budget.attempts)
        return None

    def _launch_speculation(self) -> None:
        """Admit at most one speculative branch under primary pressure."""
        self.speculation_skipped = True
        if (
            self.budget.remaining_seconds > 2
            and (not _primary_pressure(self.budget))
            and _speculative_slots.acquire(blocking=False)
        ):
            self.local_budget = self.budget.child(
                max(
                    0.1,
                    min(
                        6.5,
                        self.budget.remaining_seconds
                        - min(3.0, self.budget.remaining_seconds / 3),
                    ),
                )
            )
            self.local_budget.cancel_branch_when(lambda: _primary_pressure(self.budget))
            submitted_local = self.budget.clock()

            def speculative() -> PlannedTrip:
                self.budget.increment_metric(
                    "local_queue_seconds", self.budget.clock() - submitted_local
                )
                with self.budget.stage("local_branch"):
                    self.local_budget.check_deadline()
                    return self.local(self.local_budget)

            try:
                self.local_future = self.executor.submit(speculative)
                self.local_future.add_done_callback(
                    lambda _: _speculative_slots.release()
                )
                self.speculation_skipped = False
            except BaseException:
                _speculative_slots.release()
                raise
        if self.speculation_skipped:
            self.budget.increment_metric("portfolio_speculation_skipped", 1)

    def _wait_candidates(self) -> None:
        """Collect bounded branch results and retain verified incumbents."""
        assert self.hosted_future is not None
        pending = set()
        if self.hosted_plan is None and self.hosted_error is None:
            pending.add(self.hosted_future)
        if self.local_future is not None:
            pending.add(self.local_future)
        while pending:
            protected_hosted = (
                self.hosted_incumbent() if self.hosted_incumbent else None
            )
            protected_local = self.local_incumbent() if self.local_incumbent else None
            ready = (
                self.hosted_plan
                or self.local_plan
                or protected_hosted
                or protected_local
            )
            comparable = (
                (self.local_plan or protected_local) is not None
                and self.budget.attempts < self.budget.max_attempts
                and (ready is not None)
            )
            reserve = 2.0 if comparable else 0.5
            remaining = self.budget.remaining_seconds - reserve
            if remaining <= 0:
                break
            completed, pending = wait(
                pending, timeout=min(0.25, remaining), return_when=FIRST_COMPLETED
            )
            for future in completed:
                try:
                    plan = future.result()
                    if future is self.hosted_future:
                        self.hosted_plan = plan
                    else:
                        self.local_plan = plan
                except Exception as error:
                    if future is self.hosted_future:
                        self.hosted_error = error
                    else:
                        self.local_error = error
                    logger.info(
                        "portfolio_candidate_failed reason=%s", type(error).__name__
                    )
            if (
                self.local_future in pending
                and self.local_budget.remaining_seconds <= 0
            ):
                self.local_budget.stop_branch()
                pending.remove(self.local_future)
                self.local_error = PlanningDeadlineExceeded("local phase expired")
        if self.hosted_plan is None and self.hosted_incumbent is not None:
            self.hosted_plan = self.hosted_incumbent()
        if self.local_plan is None and self.local_incumbent is not None:
            self.local_plan = self.local_incumbent()

    def _close(self) -> None:
        """Retain capacity and routing resources until actual worker completion."""
        self.local_budget.stop_branch()
        if not self.primary_attached:
            self.release_primary()
        self.executor.shutdown(wait=False, cancel_futures=True)
        all_futures = tuple(
            future
            for future in (self.hosted_future, self.local_future)
            if future is not None
        )
        _release_slot_after(all_futures, self.slot)
        pending_futures = tuple(future for future in all_futures if not future.done())
        if pending_futures and self.on_pending is not None:
            self.on_pending(pending_futures)

    def _retry_local(self) -> None:
        """Use remaining recovery time when both initial branches failed."""
        if (
            self.hosted_plan is None
            and self.local_plan is None
            and (
                self.speculation_skipped
                or isinstance(
                    self.local_error, (NoFeasiblePlan, LocalValhallaUnavailable)
                )
            )
            and (self.budget.remaining_seconds > 4)
        ):
            try:
                self.local_plan = self.local(
                    self.budget.child(min(7.5, self.budget.remaining_seconds - 0.5))
                )
                logger.info("portfolio_local_retry_succeeded")
            except Exception as error:
                self.local_error = error
                logger.info(
                    "portfolio_local_retry_failed reason=%s", type(error).__name__
                )

    def _choose(self) -> PlannedTrip:
        """Compare costs only after verification on a common road profile."""
        self.budget.check_deadline()
        logger.info(
            "portfolio_candidates hosted=%s local=%s hosted_calls=%d remaining=%.2f",
            self.hosted_plan is not None,
            self.local_plan is not None,
            self.budget.attempts,
            self.budget.remaining_seconds,
        )
        if self.hosted_plan is None:
            if self.local_plan is not None:
                return replace(self.local_plan, outbound_attempts=self.budget.attempts)
            if isinstance(self.local_error, PlanningDeadlineExceeded) and isinstance(
                self.hosted_error, (DurationLimitExceeded, NoFeasiblePlan)
            ):
                raise self.local_error
            if self.hosted_error is not None:
                raise self.hosted_error
            if self.local_error is None:
                raise NoFeasiblePlan(
                    "no candidate could be verified in the remaining time"
                )
            raise self.local_error
        if self.local_plan is None or not self.local_plan.routed_stations:
            return replace(self.hosted_plan, outbound_attempts=self.budget.attempts)
        if tuple(item.station_id for item in self.local_plan.routed_stations) == tuple(
            item.station_id for item in self.hosted_plan.routed_stations
        ):
            return replace(self.hosted_plan, outbound_attempts=self.budget.attempts)
        if (
            self.budget.attempts >= self.budget.max_attempts
            or self.budget.remaining_seconds <= 2
        ):
            return replace(self.hosted_plan, outbound_attempts=self.budget.attempts)
        try:
            comparable_local = self.verify_local_on_hosted_profile(
                self.hosted_plan, self.local_plan, self.budget
            )
        except Exception as error:
            logger.info("portfolio_comparison_failed reason=%s", type(error).__name__)
            comparable_local = None
        chosen = (
            comparable_local
            if comparable_local is not None
            and rank_plan(comparable_local) < rank_plan(self.hosted_plan)
            else self.hosted_plan
        )
        self.budget.check_deadline()
        return replace(chosen, outbound_attempts=self.budget.attempts)
