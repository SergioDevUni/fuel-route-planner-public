"""A failed separate load generator reports its error and leaves no process."""

import multiprocessing
import socket
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scripts.benchmarking.external_clients import sustained_load


@pytest.mark.integration
def test_external_http_failure_drains_child():
    before = {process.pid for process in multiprocessing.active_children()}
    with socket.socket() as reserved:
        # A bound, non-listening port rejects HTTP without touching a provider.
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        station = SimpleNamespace(station_id="csv-1", price_per_gallon=Decimal("3"))
        probe = SimpleNamespace(
            replays=(SimpleNamespace(stations=[station]),),
            observed=[],
            calls=0,
            url=f"http://127.0.0.1:{port}",
            body=lambda index, same: {},
        )
        with pytest.raises(RuntimeError, match="ConnectError"):
            sustained_load(probe, 1, 1, same=True)
    assert {process.pid for process in multiprocessing.active_children()} == before
