"""Bound the HTTP request's wait for synchronous trip planning."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from app.planning.budget import PlanningDeadlineExceeded
from app.planning.settings import planner_settings


class PlanningCapacityExceeded(RuntimeError):
    pass


def planning_capacity() -> int:
    return planner_settings().planning_capacity


_planning_slots = threading.BoundedSemaphore(planning_capacity())
_activity = threading.Condition()
_workers: dict[Future[Any], Callable[[], None] | None] = {}
_late_workers: set[Future[Any]] = set()
_accepting_work = True
_waiting_planners = 0


def planning_activity() -> dict[str, int | bool]:
    with _activity:
        return {
            "active_planners": len(_workers),
            "late_planners": len(_late_workers),
            "accepting_work": _accepting_work,
            "waiting_planners": _waiting_planners,
        }


def shutdown_planning(timeout: float = 20) -> bool:
    """Stop admission, cancel optional work cooperatively, and drain workers."""
    global _accepting_work
    deadline = time.monotonic() + timeout
    with _activity:
        _accepting_work = False
        callbacks = tuple(_workers.values())
    for callback in callbacks:
        if callback is not None:
            callback()
    with _activity:
        while _workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _activity.wait(remaining)
        return True


def run_with_deadline[T](
    work: Callable[[], T],
    remaining_seconds: float,
    *,
    on_timeout: Callable[[], T | None] | None = None,
    on_shutdown: Callable[[], None] | None = None,
    preferred_seconds: float | None = None,
    on_preferred: Callable[[], T | None] | None = None,
    admission_wait_seconds: float = 0,
    on_admission: Callable[[float], None] | None = None,
) -> T:
    """Return or time out by the caller's deadline, with bounded late work."""
    if remaining_seconds <= 0:
        raise PlanningDeadlineExceeded("planning deadline exceeded")
    if preferred_seconds is not None and preferred_seconds <= 0:
        raise ValueError("preferred_seconds must be positive")
    if not 0 <= admission_wait_seconds <= 4:
        raise ValueError("admission wait must be between 0 and 4 seconds")
    deadline_at = time.monotonic() + remaining_seconds
    preferred_at = (
        min(deadline_at, time.monotonic() + preferred_seconds)
        if preferred_seconds is not None
        else deadline_at
    )
    slot = _planning_slots
    executor: ThreadPoolExecutor | None = None
    future: Future[T] | None = None
    _acquire_slot(slot, deadline_at, admission_wait_seconds, on_admission)
    with _activity:
        if not _accepting_work:
            slot.release()
            raise PlanningCapacityExceeded("planning capacity is draining")
        try:
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(work)
            _workers[future] = on_shutdown
        except BaseException:
            slot.release()
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            raise

    assert future is not None and executor is not None

    def finished(done: Future[Any]) -> None:
        with _activity:
            _workers.pop(done, None)
            _late_workers.discard(done)
            # Settlement and admission must expose one consistent worker count.
            # Releasing first lets a replacement register before the old entry
            # disappears, briefly overstating active capacity under contention.
            slot.release()
            _activity.notify_all()

    future.add_done_callback(finished)
    try:
        while True:
            if future.done():
                return future.result()
            now = time.monotonic()
            if now >= preferred_at and on_preferred is not None:
                incumbent = on_preferred()
                if incumbent is not None:
                    return incumbent
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise FutureTimeout()
            wait_seconds = (
                min(remaining, max(0.001, preferred_at - now))
                if now < preferred_at
                else min(remaining, 0.05)
                if on_preferred is not None
                else remaining
            )
            try:
                return future.result(timeout=wait_seconds)
            except FutureTimeout as error:
                # A worker-raised TimeoutError is a failure, not a timer tick.
                if future.done() and future.exception() is error:
                    raise
                if time.monotonic() >= deadline_at:
                    raise
    except (FutureTimeout, PlanningDeadlineExceeded) as error:
        if (
            not isinstance(error, PlanningDeadlineExceeded)
            and future.done()
            and future.exception() is error
        ):
            # TimeoutError raised inside work is not Future.result's wait
            # expiry. Do not disguise unrelated failures as deadline fallback.
            raise
        if on_timeout is not None:
            incumbent = on_timeout()
            if incumbent is not None:
                return incumbent
        raise PlanningDeadlineExceeded("planning deadline exceeded") from error
    finally:
        with _activity:
            if future in _workers:
                _late_workers.add(future)
        executor.shutdown(wait=False, cancel_futures=True)


def _acquire_slot(
    slot: threading.BoundedSemaphore,
    deadline_at: float,
    wait_seconds: float,
    on_admission: Callable[[float], None] | None,
) -> None:
    """Bound burst waiting without holding the worker completion/shutdown lock."""
    global _waiting_planners
    began = time.monotonic()
    acquired = False
    end = min(deadline_at, began + wait_seconds)
    with _activity:
        if not _accepting_work:
            raise PlanningCapacityExceeded("planning capacity is draining")
        _waiting_planners += 1
    try:
        while True:
            with _activity:
                accepting = _accepting_work
            if not accepting:
                raise PlanningCapacityExceeded("planning capacity is draining")
            if slot.acquire(blocking=False):
                if time.monotonic() >= deadline_at:
                    slot.release()
                    raise PlanningDeadlineExceeded("admission exceeded deadline")
                acquired = True
                return
            remaining = end - time.monotonic()
            if remaining <= 0:
                if time.monotonic() >= deadline_at:
                    raise PlanningDeadlineExceeded("admission exceeded deadline")
                raise PlanningCapacityExceeded("planning capacity is full")
            if slot.acquire(timeout=min(0.05, remaining)):
                if time.monotonic() >= deadline_at:
                    slot.release()
                    raise PlanningDeadlineExceeded("admission exceeded deadline")
                acquired = True
                return
    finally:
        with _activity:
            _waiting_planners -= 1
            _activity.notify_all()
        if on_admission is not None:
            try:
                on_admission(time.monotonic() - began)
            except BaseException:
                if acquired:
                    slot.release()
                raise
