"""Routing-client construction and request-owned resource lifetime."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from app.api.assets import current_candidate_hash, load_candidates
from app.api.routing_config import (
    national_local_config,
    partial_road_config,
    regional_config,
)
from app.planning.baseline_cache import (
    CachedBaselineRouter,
    baseline_route_cache,
    local_baseline_route_cache,
)
from app.planning.budget import RequestBudget
from app.planning.matrix_cache import CachedRoadMatrixProvider
from app.planning.national_refiner import NationalRoadRefiner
from app.planning.ports import RoadMatrixPort, RoutingPort
from app.routing.client import OpenRouteServiceAdapter
from app.routing.local_osrm import LocalOSRM
from app.routing.local_valhalla import BatchedLocalValhalla, LocalValhalla
from app.routing.regional_matrix import RegionalRoadMatrixProvider
from app.stations.models import DatasetVersion

logger = logging.getLogger("app.api.service")


def close_after_futures(futures: tuple[Future, ...], close: Callable[[], None]) -> None:
    """Keep request-owned routing clients alive until timed-out workers exit."""
    remaining = set(futures)
    guard = threading.Lock()

    def finished(future: Future) -> None:
        with guard:
            remaining.discard(future)
            if not remaining:
                close()

    for future in futures:
        future.add_done_callback(finished)


@dataclass
class RoutingRuntime:
    active: DatasetVersion
    adapter: OpenRouteServiceAdapter | None
    local: LocalOSRM | None
    national_local: LocalValhalla | None
    local_baseline_router: CachedBaselineRouter | None
    national_refiner: NationalRoadRefiner | None
    matrix_provider: RoadMatrixPort | None
    matrix_first: bool
    partial_config: tuple[str, str, Path, int] | None
    router: RoutingPort

    def close(self) -> None:
        for client in (self.local, self.national_local, self.adapter):
            if client is not None:
                client.close()

    def close_when_finished(self, futures: tuple[Future, ...]) -> None:
        if futures:
            close_after_futures(futures, self.close)
        else:
            self.close()


def create_runtime(candidate_hash: str | None, budget: RequestBudget) -> RoutingRuntime:
    config = budget.settings
    with budget.stage("routing_setup"):
        active = DatasetVersion.objects.filter(status="active").first()
        if active is None:
            raise FileNotFoundError("station price dataset is not ready")
        key = config.ors_api_key
        if not key and not config.national_local_router:
            raise FileNotFoundError("routing configuration is not ready")
        matrix_enabled = config.matrix_graph
        regional = regional_config(config) if matrix_enabled else None
        matrix_first = matrix_enabled and regional is None and config.matrix_first
        adapter = OpenRouteServiceAdapter(key) if key else None
        routing_data_version = config.matrix_cache_version
        router: RoutingPort | None = (
            CachedBaselineRouter(
                adapter,
                baseline_route_cache,
                f"ors-fastest-driving-car:{routing_data_version}",
                cache_selected_routes=True,
            )
            if adapter is not None
            else None
        )
        hosted_matrix = (
            CachedRoadMatrixProvider(
                adapter,
                Path(settings.BASE_DIR) / "var/matrix_cache",
                cache_version=routing_data_version,
            )
            if matrix_enabled and adapter is not None
            else None
        )
        local = None
        national_local = None
        local_baseline_router = None
        national_refiner = None
        matrix_provider: RoadMatrixPort | None = hosted_matrix
        if hosted_matrix is not None and regional is not None:
            url, version, bounds = regional
            local = LocalOSRM(url, version)
            matrix_provider = RegionalRoadMatrixProvider(local, hosted_matrix, bounds)
        national_config = national_local_config(
            candidate_hash or current_candidate_hash(), config
        )
        partial_config = (
            partial_road_config(candidate_hash or current_candidate_hash(), config)
            if national_config is None and adapter is not None
            else None
        )
        if national_config is not None or partial_config is not None:
            if national_config is not None:
                national_url, national_version, national_database = national_config
            else:
                assert partial_config is not None
                national_url, national_version, national_database = partial_config[:3]
            using_national_local_router = (
                national_config is not None and config.national_local_router
            )
            national_local = LocalValhalla(
                national_url,
                national_version,
                request_timeout_seconds=8,
            )
            local_baseline_router = CachedBaselineRouter(
                national_local,
                local_baseline_route_cache,
                f"valhalla-auto:{national_url}:{national_version}",
                cache_selected_routes=True,
            )
            national_refiner = NationalRoadRefiner(
                national_database,
                load_candidates(
                    active.source_sha256,
                    candidate_hash or current_candidate_hash(),
                    budget,
                ),
                national_local,
                allow_partial=national_config is None,
                verification_reserve_seconds=(
                    2 if using_national_local_router else 4.5
                ),
            )
            if using_national_local_router:
                router = local_baseline_router
                matrix_provider = BatchedLocalValhalla(national_local)
                matrix_first = True
        if router is None:
            raise FileNotFoundError("routing configuration is not ready")
    return RoutingRuntime(
        active,
        adapter,
        local,
        national_local,
        local_baseline_router,
        national_refiner,
        matrix_provider,
        matrix_first,
        partial_config,
        router,
    )
