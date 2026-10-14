"""Local directed matrix cache and hosted-attempt accounting."""

from decimal import Decimal

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.matrix_cache import CachedRoadMatrixProvider

POINTS = (Coordinates(40, -90), Coordinates(40, -89))
MATRIX = RoadDistanceMatrix(
    ((Decimal(0), Decimal(1000)), (Decimal(1100), Decimal(0))),
    (Decimal(0), Decimal(1)),
    (Decimal(0), Decimal(1)),
    ((Decimal(0), Decimal(80)), (Decimal(85), Decimal(0))),
)


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def matrix(self, points, budget):
        budget.consume_attempt()
        self.calls += 1
        return MATRIX


def test_cache_hit_preserves_third_hosted_attempt(tmp_path) -> None:
    provider = FakeProvider()
    cache = CachedRoadMatrixProvider(provider, tmp_path, clock=lambda: 1000)
    first = RequestBudget()
    assert cache.matrix(POINTS, first) == MATRIX
    assert first.attempts == 1

    second = RequestBudget()
    second.consume_attempt()  # baseline directions
    assert cache.matrix(POINTS, second) == MATRIX
    assert second.attempts == 1
    second.consume_attempt()  # selected final directions
    second.consume_correction()
    second.consume_attempt()  # one changed-waypoint repair
    assert second.attempts == 3
    assert provider.calls == 1


def test_cache_version_ttl_point_order_and_corruption_force_new_matrix(
    tmp_path,
) -> None:
    provider = FakeProvider()
    now = [1000.0]
    cache = CachedRoadMatrixProvider(
        provider, tmp_path, clock=lambda: now[0], max_age_seconds=100
    )
    cache.matrix(POINTS, RequestBudget())
    cache.matrix(POINTS, RequestBudget())
    assert provider.calls == 1

    cache.matrix(tuple(reversed(POINTS)), RequestBudget())
    assert provider.calls == 2
    CachedRoadMatrixProvider(
        provider, tmp_path, cache_version="new-road-data", clock=lambda: now[0]
    ).matrix(POINTS, RequestBudget())
    assert provider.calls == 3

    now[0] += 101
    cache.matrix(POINTS, RequestBudget())
    assert provider.calls == 4
    cache_path = tmp_path / f"{cache._key(POINTS)}.json"
    cache_path.write_text("{broken", encoding="utf-8")
    cache.matrix(POINTS, RequestBudget())
    assert provider.calls == 5
