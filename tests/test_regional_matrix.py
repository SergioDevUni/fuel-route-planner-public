"""Regional graph saves a hosted attempt only when all points are covered."""

from decimal import Decimal

import httpx

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.routing.local_osrm import LocalOSRM
from app.routing.regional_matrix import RegionalRoadMatrixProvider

POINTS = (Coordinates(38.56, -75.56), Coordinates(39.66, -75.62))
BOUNDS = (-75.8, 38.4, -75.3, 39.9)


class HostedMatrix:
    def matrix(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> RoadDistanceMatrix:
        budget.consume_attempt()
        return RoadDistanceMatrix(
            ((Decimal(0), Decimal(2)), (Decimal(2), Decimal(0))),
            (Decimal(0), Decimal(0)),
            (Decimal(0), Decimal(0)),
            ((Decimal(0), Decimal(2)), (Decimal(2), Decimal(0))),
        )


def local(payload: dict) -> LocalOSRM:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    return LocalOSRM("http://local:5000", "de-2026-10-09", client=client)


def test_covered_local_matrix_preserves_hosted_repair_attempt() -> None:
    payload = {
        "code": "Ok",
        "sources": [{"distance": 10}, {"distance": 15}],
        "destinations": [{"distance": 11}, {"distance": 16}],
        "distances": [[0, 137660.6], [137107.9, 0]],
        "durations": [[0, 6000], [5900, 0]],
    }
    budget = RequestBudget()
    budget.consume_attempt()  # Baseline directions.
    provider = RegionalRoadMatrixProvider(local(payload), HostedMatrix(), BOUNDS)
    matrix = provider.matrix(POINTS, budget)
    assert matrix.distances_meters[0][1] == Decimal("137660.6")
    assert matrix.durations_seconds[1][0] == Decimal(5900)
    assert matrix.destination_snap_meters[1] == Decimal(16)
    assert budget.attempts == 1
    budget.consume_attempt()  # Final directions.
    budget.consume_attempt()  # One corrected directions route still fits.
    assert budget.attempts == 3


def test_outside_region_uses_hosted_matrix() -> None:
    provider = RegionalRoadMatrixProvider(local({}), HostedMatrix(), BOUNDS)
    budget = RequestBudget()
    outside = (POINTS[0], Coordinates(40.0, -75.6))
    matrix = provider.matrix(outside, budget)
    assert matrix.distances_meters[0][1] == Decimal(2)
    assert budget.attempts == 1


def test_bad_local_snap_falls_back_to_hosted_matrix() -> None:
    payload = {
        "code": "Ok",
        "sources": [{"distance": 10}, {"distance": 1_000}],
        "destinations": [{"distance": 11}, {"distance": 16}],
        "distances": [[0, 1], [1, 0]],
        "durations": [[0, 1], [1, 0]],
    }
    provider = RegionalRoadMatrixProvider(local(payload), HostedMatrix(), BOUNDS)
    budget = RequestBudget()
    provider.matrix(POINTS, budget)
    assert budget.attempts == 1
