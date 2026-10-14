"""Exact endpoint blocks survive retries without crossing road graph versions."""

from decimal import Decimal
from pathlib import Path

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.endpoint_block_cache import EndpointBlockCache
from app.routing.local_valhalla import LocalRoadBlock


class LocalStub:
    def __init__(self, graph_version: str) -> None:
        self.graph_version = graph_version
        self.calls = 0

    def matrix_block(
        self,
        sources: tuple[Coordinates, ...],
        targets: tuple[Coordinates, ...],
        budget: RequestBudget,
    ) -> LocalRoadBlock:
        self.calls += 1
        return LocalRoadBlock(
            tuple(tuple(Decimal(1000 + self.calls) for _ in targets) for _ in sources),
            tuple(tuple(Decimal(120) for _ in targets) for _ in sources),
        )


def test_endpoint_cache_reuses_only_exact_versioned_block(tmp_path: Path) -> None:
    start = (Coordinates(35, -106),)
    targets = (Coordinates(36, -105), Coordinates(37, -104))
    local = LocalStub("graph-a")
    cache = EndpointBlockCache(tmp_path, local)  # type: ignore[arg-type]
    first = cache.matrix_block(start, targets, RequestBudget())
    assert cache.matrix_block(start, targets, RequestBudget()) == first
    assert local.calls == 1

    changed = LocalStub("graph-b")
    new_cache = EndpointBlockCache(tmp_path, changed)  # type: ignore[arg-type]
    new_cache.matrix_block(start, targets, RequestBudget())
    assert changed.calls == 1
    cache.matrix_block(start, tuple(reversed(targets)), RequestBudget())
    assert local.calls == 2
