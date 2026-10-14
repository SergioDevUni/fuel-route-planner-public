import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.planning.budget import RequestBudget
from app.routing.client import OpenRouteServiceAdapter, RoutingUnavailable
from scripts.road_replay import RoadReplay, encode, road_key


def test_recorded_route_preserves_exact_legs_and_unknown_queries_do_not_call_provider(
    tmp_path, monkeypatch
):
    points = (Coordinates(40, -80), Coordinates(41, -79))
    route = Route(
        points,
        tuple(RouteWaypoint(p, p, i, Decimal(0)) for i, p in enumerate(points)),
        (RouteLeg(Decimal("123.456789"), Decimal("20.1234")),),
        Decimal("123.456789"),
        Decimal("20.1234"),
        "recorded-test-road-version",
        datetime.now(UTC),
    )
    fixture = tmp_path / "roads.json"
    fixture.write_text(
        json.dumps(
            {
                "schema": 1,
                "entries": {
                    road_key("route", points): {"seconds": 0, "facts": asdict(route)}
                },
            },
            default=encode,
        ),
        encoding="utf-8",
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("no provider calls in replay")

    monkeypatch.setattr(OpenRouteServiceAdapter, "route", unexpected)
    budget = RequestBudget()
    with RoadReplay(replay=fixture):
        assert OpenRouteServiceAdapter.route(None, points, budget) == route
        with pytest.raises(RoutingUnavailable, match="unavailable"):
            OpenRouteServiceAdapter.route(None, tuple(reversed(points)), budget)
    assert budget.attempts == 2
    assert budget.metrics["road_replay_missing"] == 1
