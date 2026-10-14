"""Local matrix contract: direction, snap bounds, and malformed responses."""

import httpx
import pytest

from app.domain.routing import Coordinates
from app.routing.local_osrm import (
    LocalOSRM,
    LocalRoadSnapFailed,
    LocalRoadUnavailable,
)

POINTS = (Coordinates(38.56, -75.56), Coordinates(39.66, -75.62))


def adapter(payload: dict) -> LocalOSRM:
    payload.setdefault("destinations", payload.get("sources"))
    payload.setdefault("durations", payload.get("distances"))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    return LocalOSRM("http://local:5000", "de-2026-10-06", client=client)


def test_directed_distances_are_not_symmetrized() -> None:
    engine = adapter(
        {
            "code": "Ok",
            "sources": [{"distance": 20}, {"distance": 10}],
            "distances": [[0, 137660.6], [137107.9, 0]],
        }
    )
    result = engine.matrix(POINTS)
    assert result.distances_meters[0][1] != result.distances_meters[1][0]
    assert result.graph_version == "de-2026-10-06"
    assert result.distance_semantics == "distance_of_fastest_car_route"
    assert result.durations_seconds[0][1] == 137660.6


def test_far_destination_snap_rejected() -> None:
    engine = adapter(
        {
            "code": "Ok",
            "sources": [{"distance": 20}, {"distance": 10}],
            "destinations": [{"distance": 20}, {"distance": 1_000}],
            "distances": [[0, 1], [1, 0]],
        }
    )
    with pytest.raises(LocalRoadSnapFailed):
        engine.matrix(POINTS)


def test_far_regional_snap_rejected() -> None:
    engine = adapter(
        {
            "code": "Ok",
            "sources": [{"distance": 20}, {"distance": 67_364}],
            "distances": [[0, 1], [1, 0]],
        }
    )
    with pytest.raises(LocalRoadSnapFailed):
        engine.matrix(POINTS)


def test_malformed_matrix_rejected() -> None:
    engine = adapter(
        {
            "code": "Ok",
            "sources": [{"distance": 20}, {"distance": 10}],
            "distances": [[0, 1]],
        }
    )
    with pytest.raises(LocalRoadUnavailable):
        engine.matrix(POINTS)
