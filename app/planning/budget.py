"""Request-scoped hosted-attempt and planning-time limits."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from threading import Event, Lock
from typing import Any

from app.planning.optimization import BALANCED, OptimizationPolicy, optimization_policy
from app.planning.settings import PlannerSettings, planner_settings


class RoutingBudgetExceeded(RuntimeError):
    pass


class PlanningDeadlineExceeded(TimeoutError):
    pass


@dataclass(slots=True)
class _SharedCounters:
    lock: Lock = field(default_factory=Lock)
    attempts: int = 0
    corrections: int = 0
    payloads: dict[
        str, tuple[tuple[Decimal, Decimal, int, tuple[str, ...]], dict[str, Any]]
    ] = field(default_factory=dict)
    stages: dict[str, float] = field(default_factory=dict)
    first_verified_seconds: float | None = None
    first_verified_from_cache: bool | None = None
    interrupted: bool = False
    response_target_at: float | None = None
    planning_deadline_at: float | None = None
    policy: OptimizationPolicy | None = None
    finalization_reserve: float = 0.0
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    active_stages: dict[str, list[float]] = field(default_factory=dict)
    metrics: dict[str, int | float] = field(default_factory=dict)
    audit: dict[str, tuple[str, ...]] = field(default_factory=dict)
    searches: list[dict[str, Any]] = field(default_factory=list)
    cpu_stages: dict[str, float] = field(default_factory=dict)
    payload_sink: (
        Callable[
            [str, tuple[Decimal, Decimal, int, tuple[str, ...]], dict[str, Any]], None
        ]
        | None
    ) = None
    completed_payload_sink: Callable[[dict[str, Any]], None] | None = None


@dataclass(slots=True)
class RequestBudget:
    max_attempts: int = 4
    deadline_seconds: float = 15.0
    target_seconds: float = 10.0
    clock: Callable[[], float] = time.monotonic
    settings: PlannerSettings = field(default_factory=planner_settings, repr=False)
    _started: float = field(init=False, repr=False)
    _deadline_at: float = field(init=False, repr=False)
    _shared: _SharedCounters = field(init=False, repr=False)
    _branch_stops: tuple[Event, ...] = field(init=False, repr=False)
    _cancel_probe: Callable[[], bool] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 4:
            raise ValueError("invalid request budget")
        if self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if self.target_seconds <= 0:
            raise ValueError("target_seconds must be positive")
        self._started = self.clock()
        self._deadline_at = self._started + self.deadline_seconds
        self._shared = _SharedCounters()
        self._branch_stops = (Event(),)

    def child(
        self, deadline_seconds: float, *, max_attempts: int | None = None
    ) -> "RequestBudget":
        """Create a phase deadline while sharing atomic hosted-call counters."""
        if deadline_seconds <= 0:
            raise ValueError("child deadline must be positive")
        child = RequestBudget(
            max_attempts=(
                min(self.max_attempts, max_attempts)
                if max_attempts is not None
                else self.max_attempts
            ),
            deadline_seconds=deadline_seconds,
            target_seconds=self.target_seconds,
            clock=self.clock,
            settings=self.settings,
        )
        child._deadline_at = min(self._deadline_at, child._deadline_at)
        child._started = self._started
        child._shared = self._shared
        child._branch_stops = (*self._branch_stops, Event())
        child._cancel_probe = self._cancel_probe
        return child

    @property
    def optimization(self) -> OptimizationPolicy:
        return self._shared.policy or BALANCED

    def configure_mode(self, mode: str) -> None:
        """Bind one policy before work; children retain its absolute cutoffs."""
        policy = optimization_policy(mode)
        with self._shared.lock:
            if self._shared.policy is not None:
                if self._shared.policy != policy:
                    raise ValueError("request branches must share optimization mode")
                return
            self._shared.policy = policy
            target = (
                self._deadline_at
                if policy.use_full_window
                else min(self._deadline_at, self._started + self.target_seconds)
            )
            self._shared.response_target_at = target - self._shared.finalization_reserve

    @property
    def attempts(self) -> int:
        with self._shared.lock:
            return self._shared.attempts

    @property
    def corrections(self) -> int:
        with self._shared.lock:
            return self._shared.corrections

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline_at - self.clock())

    @property
    def target_remaining_seconds(self) -> float:
        """Optional improvement window, including HTTP finalization headroom."""
        return max(
            0.0,
            min(
                self._deadline_at,
                self._shared.response_target_at
                if self._shared.response_target_at is not None
                else self._started + self.target_seconds,
            )
            - self.clock(),
        )

    @property
    def planning_remaining_seconds(self) -> float:
        """Hard recovery window before the response's finalization reserve."""
        return max(
            0.0,
            min(
                self._deadline_at,
                self._shared.planning_deadline_at
                if self._shared.planning_deadline_at is not None
                else self._deadline_at,
            )
            - self.clock(),
        )

    def reserve_finalization(self, seconds: float) -> None:
        """Publish absolute response cutoffs once, shared by every child phase."""
        if not 0 <= seconds < self.remaining_seconds:
            raise ValueError("finalization reserve must fit the request deadline")
        with self._shared.lock:
            self._shared.finalization_reserve = seconds
            self._shared.response_target_at = (
                self._deadline_at
                if self.optimization.use_full_window
                else min(self._deadline_at, self._started + self.target_seconds)
            ) - seconds
            self._shared.planning_deadline_at = self._deadline_at - seconds

    def check_deadline(self) -> None:
        if self.remaining_seconds <= 0 or self.cancelled:
            raise PlanningDeadlineExceeded("planning deadline exceeded")

    def check_finalization_deadline(self, margin_seconds: float = 0) -> None:
        """Stop optional planning while retaining the response's absolute deadline."""
        if self.remaining_seconds <= margin_seconds:
            raise PlanningDeadlineExceeded("response finalization deadline exceeded")

    def stop_work(self) -> None:
        """Cooperatively stop late work after the response snapshot is chosen."""
        with self._shared.lock:
            self._shared.interrupted = True

    @property
    def cancelled(self) -> bool:
        return (
            self._shared.interrupted
            or any(e.is_set() for e in self._branch_stops)
            or bool(self._cancel_probe and self._cancel_probe())
        )

    def stop_branch(self) -> None:
        self._branch_stops[-1].set()

    def cancel_branch_when(self, probe: Callable[[], bool]) -> None:
        self._cancel_probe = probe

    def increment_metric(self, name: str, amount: int | float) -> None:
        with self._shared.lock:
            self._shared.metrics[name] = self._shared.metrics.get(name, 0) + amount

    def record_search(
        self,
        *,
        scope: str,
        offers: int,
        complete: bool,
        expansions: int,
        coverage: dict[str, int] | None = None,
    ) -> None:
        with self._shared.lock:
            self._shared.searches.append(
                {
                    "scope": scope,
                    "offer_count": offers,
                    "complete": complete,
                    "expansions": expansions,
                    "termination": "scope_exhausted"
                    if complete
                    else "time_or_expansion_limit",
                    **(
                        {"offer_coverage": dict(coverage)}
                        if coverage is not None
                        else {}
                    ),
                }
            )

    def search_evidence(self) -> list[dict[str, Any]]:
        with self._shared.lock:
            return deepcopy(self._shared.searches)

    def audit_offers(self, stage: str, ids: Iterator[str]) -> None:
        offers = tuple(sorted(set(ids)))
        with self._shared.lock:
            self._shared.audit[stage] = offers

    @property
    def metrics(self) -> dict[str, int | float]:
        with self._shared.lock:
            return dict(self._shared.metrics)

    @property
    def offer_audit(self) -> dict[str, tuple[str, ...]]:
        with self._shared.lock:
            return dict(self._shared.audit)

    @property
    def completed_stage_cpu_seconds(self) -> dict[str, float]:
        with self._shared.lock:
            return {k: round(v, 4) for k, v in self._shared.cpu_stages.items()}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        began = self.clock()
        cpu_began = time.thread_time()
        with self._shared.lock:
            self._shared.active_stages.setdefault(name, []).append(began)
        try:
            yield
        finally:
            with self._shared.lock:
                self._shared.cpu_stages[name] = (
                    self._shared.cpu_stages.get(name, 0.0)
                    + time.thread_time()
                    - cpu_began
                )
                self._shared.stages[name] = (
                    self._shared.stages.get(name, 0.0) + self.clock() - began
                )
                self._shared.active_stages[name].remove(began)

    @property
    def stage_seconds(self) -> dict[str, float]:
        with self._shared.lock:
            names = self._shared.stages.keys() | self._shared.active_stages.keys()
            now = self.clock()
            return {
                key: round(
                    self._shared.stages.get(key, 0.0)
                    + sum(
                        now - began for began in self._shared.active_stages.get(key, ())
                    ),
                    4,
                )
                for key in names
            }

    @property
    def first_verified_seconds(self) -> float | None:
        return self._shared.first_verified_seconds

    @property
    def first_verified_from_cache(self) -> bool | None:
        return self._shared.first_verified_from_cache

    def publish_verified_payload(
        self,
        payload: dict[str, Any],
        rank: tuple[Decimal, Decimal, int, tuple[str, ...]],
        profile: str = "hosted",
        *,
        from_cache: bool = False,
    ) -> None:
        """Only the final-policy/serialization boundary may publish a snapshot."""
        copied = deepcopy(payload)
        self.check_deadline()
        with self._shared.lock:
            old = self._shared.payloads.get(profile)
            if self._shared.interrupted:
                raise PlanningDeadlineExceeded("response already finalized")
            if old is None or rank <= old[0]:
                self._shared.payloads[profile] = (rank, copied)
            if self._shared.first_verified_seconds is None:
                self._shared.first_verified_seconds = self.clock() - self._started
                self._shared.first_verified_from_cache = from_cache
        self.record_candidate(rank[0], profile=profile, verified=True)
        sink = self._shared.payload_sink
        if sink is not None:
            sink(profile, rank, copied)

    def set_payload_sink(
        self,
        sink: Callable[
            [str, tuple[Decimal, Decimal, int, tuple[str, ...]], dict[str, Any]], None
        ],
    ) -> None:
        self._shared.payload_sink = sink

    def set_completed_payload_sink(
        self, sink: Callable[[dict[str, Any]], None]
    ) -> None:
        self._shared.completed_payload_sink = sink

    def cache_completed_payload(self, payload: dict[str, Any]) -> None:
        """The HTTP decision may cache its verified snapshot after cancellation."""
        if sink := self._shared.completed_payload_sink:
            sink(deepcopy(payload))

    def record_candidate(
        self, cost: Decimal, *, profile: str = "hosted", verified: bool = False
    ) -> None:
        with self._shared.lock:
            if len(self._shared.trajectory) < 256:
                self._shared.trajectory.append(
                    {
                        "seconds": round(self.clock() - self._started, 4),
                        "cost": str(cost),
                        "profile": profile,
                        "verified": verified,
                    }
                )

    @property
    def cost_trajectory(self) -> list[dict[str, Any]]:
        with self._shared.lock:
            return deepcopy(self._shared.trajectory)

    @property
    def verified_routing_profile(self) -> str | None:
        """Retain routing provenance for server-side measurements."""
        with self._shared.lock:
            if self._shared.payloads.get("hosted") is not None:
                return "hosted"
            if self._shared.payloads.get("local") is not None:
                return "local"
            return None

    def verified_payload(self) -> dict[str, Any] | None:
        with self._shared.lock:
            # Costs from different road profiles are never ranked against each
            # other. Hosted feasibility is preferred when both are available.
            entry = self._shared.payloads.get("hosted") or self._shared.payloads.get(
                "local"
            )
            return deepcopy(entry[1]) if entry is not None else None

    def consume_attempt(self) -> None:
        with self._shared.lock:
            self.check_deadline()
            if self._shared.attempts >= self.max_attempts:
                raise RoutingBudgetExceeded("routing attempt budget exhausted")
            self._shared.attempts += 1

    def consume_correction(self) -> None:
        with self._shared.lock:
            self.check_deadline()
            self._shared.corrections += 1
