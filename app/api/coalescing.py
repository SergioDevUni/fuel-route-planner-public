"""Same-input HTTP followers wait without occupying planner worker slots."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any

from app.planning.budget import RequestBudget


@dataclass
class _RequestFlight:
    finished: Event = field(default_factory=Event)
    payload: dict[str, Any] | None = None
    error: BaseException | None = None


class RequestCoalescer:
    def __init__(self) -> None:
        self._guard = Lock()
        self._flights: dict[str, _RequestFlight] = {}

    def run(
        self,
        key: str,
        work: Callable[[], dict[str, Any]],
        budget: RequestBudget,
    ) -> dict[str, Any]:
        with self._guard:
            flight = self._flights.get(key)
            owner = flight is None
            if flight is None:
                flight = _RequestFlight()
                self._flights[key] = flight
        if not owner:
            with budget.stage("request_coalescing_wait"):
                while not flight.finished.wait(min(0.05, budget.remaining_seconds)):
                    budget.check_deadline()
                budget.check_deadline()
            if flight.error is not None:
                raise flight.error
            assert flight.payload is not None
            budget.increment_metric("coalesced_result_reuses", 1)
            return deepcopy(flight.payload)
        try:
            payload = work()
            flight.payload = deepcopy(payload)
            return payload
        except BaseException as error:
            flight.error = error
            raise
        finally:
            with self._guard:
                self._flights.pop(key, None)
                flight.finished.set()


request_coalescer = RequestCoalescer()
