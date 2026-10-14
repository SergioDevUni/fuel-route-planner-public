"""Captured-road replay preserves directed facts and fails on unknown queries."""

import json
from dataclasses import asdict
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget, RoutingBudgetExceeded
from app.routing.client import RoutingUnavailable
from scripts.benchmarking.offline_replay import FrozenRoads
from tests.support.planning import FINISH, START, STATIONS, _route


@pytest.fixture
def roads():
    station = STATIONS[0]
    graph = {
        "stations": [{"station_id": station.station_id}],
        "distances_meters": [["0", "10", "30"], ["15", "0", None], ["40", "20", "0"]],
        "durations_seconds": [["0", "1", "3"], ["2", "0", None], ["4", "2", "0"]],
    }
    facts = {
        "routes": {
            "case": [
                {
                    "points": [asdict(START), asdict(FINISH)],
                    "route": asdict(_route((START, FINISH), (500,))),
                }
            ]
        },
        "graphs": {"case": graph},
    }
    frozen = json.loads(json.dumps(facts, default=str))
    return FrozenRoads(frozen, "case", SimpleNamespace(stations=(station,)))


def test_replay_uses_exact_directed_edges_and_preserves_unknown_edges(roads):
    budget = RequestBudget()
    matrix = roads.matrix((FINISH, STATIONS[0].coordinates, START), budget)
    assert matrix.distances_meters == (
        (Decimal(0), Decimal(20), Decimal(40)),
        (None, Decimal(0), Decimal(15)),
        (Decimal(30), Decimal(10), Decimal(0)),
    )
    assert matrix.source_snap_meters == (Decimal(1000), Decimal(250), Decimal(1000))
    assert budget.attempts == 1


@pytest.mark.parametrize("kind", ["route", "matrix"])
def test_unknown_replay_query_fails_and_keeps_attempt_cap(roads, kind):
    budget = RequestBudget(max_attempts=1)
    with pytest.raises(RoutingUnavailable, match="unavailable"):
        getattr(roads, kind)((START, Coordinates(42, -92)), budget)
    assert budget.attempts == 1 and budget.metrics["road_replay_missing"] == 1
    with pytest.raises(RoutingBudgetExceeded):
        getattr(roads, kind)((START, Coordinates(42, -92)), budget)
