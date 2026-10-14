"""Result-write admission is fair and never leaks ownership after a timeout."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest

from app.api.result_writer import ResultWriter


def wait_for_queue(writer, count):
    until = monotonic() + 1
    while monotonic() < until:
        with writer._guard:
            if len(writer._waiters) == count:
                return
        sleep(0.001)
    pytest.fail("caller did not reach result-write queue")


def test_fifo_ownership_cannot_be_stolen_by_a_new_arrival():
    writer, order = ResultWriter(), []
    assert writer.acquire()
    first_started, release_first = Event(), Event()

    def write(index):
        assert writer.acquire(timeout=2)
        try:
            order.append(index)
            if index == 0:
                first_started.set()
                assert release_first.wait(2)
        finally:
            writer.release()

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(write, 0)
        wait_for_queue(writer, 1)
        second = pool.submit(write, 1)
        wait_for_queue(writer, 2)
        writer.release()
        assert first_started.wait(1)
        third = pool.submit(write, 2)
        try:
            wait_for_queue(writer, 2)
        finally:
            release_first.set()
        for pending in (first, second, third):
            pending.result(timeout=1)
    assert order == [0, 1, 2]
    assert not writer._active and not writer._waiters


def test_timeout_removes_waiter_and_next_caller_still_gets_ownership():
    writer = ResultWriter()
    assert writer.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert not pool.submit(writer.acquire, timeout=0.01).result(timeout=1)
    assert not writer._waiters
    writer.release()
    with writer:
        assert writer._active
    assert not writer._active


def test_timeout_release_race_observes_handoff_and_releases_it(monkeypatch):
    writer = ResultWriter()
    assert writer.acquire()

    class RacingEvent(Event):
        def wait(self, timeout=None):
            writer.release()
            return False  # Report timeout just as the event receives ownership.

    monkeypatch.setattr("app.api.result_writer.Event", RacingEvent)
    assert writer.acquire(timeout=0)
    assert writer._active and not writer._waiters
    writer.release()
    assert writer.acquire(timeout=0)
    writer.release()


def test_interrupted_wait_releases_handed_off_ownership(monkeypatch):
    writer = ResultWriter()
    assert writer.acquire()

    class InterruptedEvent(Event):
        def wait(self, timeout=None):
            writer.release()
            raise KeyboardInterrupt

    monkeypatch.setattr("app.api.result_writer.Event", InterruptedEvent)
    with pytest.raises(KeyboardInterrupt):
        writer.acquire()
    assert not writer._active and not writer._waiters
    assert writer.acquire(timeout=0)
    writer.release()
