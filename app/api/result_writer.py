"""FIFO admission for the single process-local SQLite result writer."""

from collections import deque
from threading import Event, Lock


class ResultWriter:
    """Hand ownership directly to queued callers; timed-out callers leave cleanly."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._active = False
        self._waiters: deque[Event] = deque()

    def acquire(self, timeout: float = -1) -> bool:
        if timeout < 0 and timeout != -1:
            raise ValueError("timeout must be nonnegative or -1")
        with self._guard:
            if not self._active:
                self._active = True
                return True
            waiter = Event()
            self._waiters.append(waiter)
        try:
            if waiter.wait(None if timeout == -1 else timeout):
                return True
        except BaseException:
            with self._guard:
                if waiter.is_set():
                    self._release_locked()
                else:
                    self._waiters.remove(waiter)
            raise
        with self._guard:
            # A simultaneous release may already have handed us ownership.
            if waiter.is_set():
                return True
            self._waiters.remove(waiter)
            return False

    def _release_locked(self) -> None:
        if self._waiters:
            self._waiters.popleft().set()
        else:
            self._active = False

    def release(self) -> None:
        with self._guard:
            if not self._active:
                raise RuntimeError("release of inactive result writer")
            self._release_locked()

    def __enter__(self) -> None:
        self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()
