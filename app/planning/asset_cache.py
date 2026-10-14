"""Bounded immutable asset reuse with one owner per concurrent cache miss."""

from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from threading import Lock

from app.planning.budget import RequestBudget


class ImmutableAssetCache[T]:
    """Share construction, while each waiter retains its own request deadline."""

    def __init__(self, name: str, max_entries: int = 4) -> None:
        if max_entries < 1:
            raise ValueError("asset cache must retain at least one entry")
        self.name, self.max_entries = name, max_entries
        self._guard = Lock()
        self._entries: OrderedDict[Hashable, T] = OrderedDict()
        self._pending: dict[Hashable, Future[T]] = {}
        self._generation = 0

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()
            self._generation += 1

    def get(
        self, key: Hashable, load: Callable[[], T], budget: RequestBudget | None = None
    ) -> T:
        if budget is not None:
            budget.check_deadline()
        with self._guard:
            if key in self._entries:
                self._entries.move_to_end(key)
                if budget is not None:
                    budget.increment_metric(f"{self.name}_asset_hits", 1)
                return self._entries[key]
            future = self._pending.get(key)
            owner = future is None
            if future is None:
                future = Future()
                self._pending[key] = future
            generation = self._generation
        if not owner:
            if budget is None:
                return future.result()
            budget.increment_metric(f"{self.name}_asset_waiters", 1)
            with budget.stage(f"{self.name}_asset_wait"):
                while True:
                    budget.check_deadline()
                    try:
                        result = future.result(
                            timeout=min(0.05, budget.remaining_seconds)
                        )
                    except FutureTimeout:
                        if future.done():
                            # A loader's TimeoutError is not a waiter timeout.
                            return future.result()
                        continue
                    budget.check_deadline()
                    return result
        try:
            if budget is None:
                result = load()
            else:
                budget.increment_metric(f"{self.name}_asset_builds", 1)
                with budget.stage(f"{self.name}_asset_build"):
                    result = load()
            with self._guard:
                if generation == self._generation:
                    self._entries[key] = result
                    self._entries.move_to_end(key)
                    if len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
            future.set_result(result)
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self._guard:
                del self._pending[key]
        if budget is not None:
            budget.check_deadline()
        return result
