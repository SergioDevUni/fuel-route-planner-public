"""Improve verified plans using complete sources from a versioned road cache."""

import logging
import sqlite3
from decimal import Decimal
from pathlib import Path

from app.domain.routing import Coordinates, Route
from app.planning.budget import RequestBudget
from app.planning.candidates import (
    CandidateIndex,
    ProjectedCandidate,
    StationCandidate,
    discovery_context,
)
from app.planning.endpoint_block_cache import EndpointBlockCache
from app.planning.graph_search import search_fuel_graph
from app.planning.national_graph import (
    NationalRoadSelection,
    StationRoadGraphUnavailable,
    build_trip_road_graph,
    completed_road_points,
)
from app.planning.national_scope import screen_csv_offers
from app.planning.quality_evidence import offer_coverage
from app.planning.query_road_graph import QueryRoadSelection, build_query_road_graph
from app.routing.local_valhalla import LocalValhalla, LocalValhallaUnavailable

logger = logging.getLogger(__name__)


class NationalRoadRefiner:
    def __init__(
        self,
        database: Path,
        candidates: CandidateIndex,
        local: LocalValhalla,
        *,
        allow_partial: bool = False,
        verification_reserve_seconds: float = 4.5,
    ) -> None:
        if not 0 < verification_reserve_seconds < 15:
            raise ValueError("verification reserve must fit planning deadline")
        self.database = database
        self.candidates = candidates
        self.local = local
        self.allow_partial = allow_partial
        self.verification_reserve_seconds = verification_reserve_seconds
        self.endpoint_cache = EndpointBlockCache(
            database.parent / "endpoint_blocks", local
        )

    def propose(
        self,
        start: Coordinates,
        finish: Coordinates,
        initial: Decimal,
        reserve: Decimal,
        incumbent_cost: Decimal | None,
        max_total_seconds: Decimal,
        max_total_meters: Decimal | None,
        budget: RequestBudget,
        *,
        baseline: Route | None = None,
        seed_ids: tuple[str, ...] = (),
        incumbent_route: Route | None = None,
    ) -> tuple[ProjectedCandidate, ...] | None:
        if (
            incumbent_cost is not None and incumbent_cost <= 0
        ) or budget.remaining_seconds <= 2.5:
            return None
        verification_reserve = min(
            self.verification_reserve_seconds, budget.remaining_seconds / 3
        )
        scope = (
            screen_csv_offers(
                self.candidates.stations,
                start,
                finish,
                initial,
                reserve,
                incumbent_cost,
                price_access_bound=True,
            )
            if incumbent_cost is not None
            else None
        )
        eligible = (
            tuple(item.station for item in scope.possible_offers)
            if scope is not None
            else self.candidates.stations
        )
        selected = self._select_graph(
            eligible,
            start,
            finish,
            initial,
            reserve,
            budget,
            verification_reserve,
            baseline,
            seed_ids,
            incumbent_route,
            max_total_seconds,
            max_total_meters,
        )
        if selected is None:
            return None
        with budget.stage("available_source_fuel_search"):
            result = search_fuel_graph(
                selected.graph,
                initial,
                reserve,
                max_total_meters=max_total_meters,
                max_total_seconds=max_total_seconds,
                max_expansions=100_000,
                stop_when=lambda: budget.cancelled
                or budget.remaining_seconds <= verification_reserve,
                on_improvement=lambda solution: budget.record_candidate(
                    solution.purchase_cost, profile="local"
                ),
            )
        budget.record_search(
            scope="available_local_road_sources",
            offers=len(selected.retained),
            complete=result.complete,
            expansions=result.expansions,
            coverage=offer_coverage(
                (s.station_id for s in self.candidates.stations),
                (s.station_id for s in eligible),
                (s.station_id for s in selected.retained),
                selected.unknown_ids
                if isinstance(selected, QueryRoadSelection)
                else (),
            ),
        )
        logger.info(
            "national_search csv_offers=%d eligible=%d retained=%d unavailable=%d "
            "expansions=%d complete=%s",
            len(self.candidates.stations),
            len(eligible),
            len(selected.retained),
            len(selected.unavailable_ids),
            result.expansions,
            result.complete,
        )
        solution = result.solution
        if solution is None or not solution.station_ids:
            return None
        if incumbent_cost is not None and solution.purchase_cost >= incumbent_cost:
            return None
        by_id = {station.station_id: station for station in selected.retained}
        progress = Decimal(0)
        proposal: list[ProjectedCandidate] = []
        for index, station_id in enumerate(solution.station_ids):
            progress += solution.leg_distances_meters[index]
            proposal.append(
                ProjectedCandidate(
                    by_id[station_id],
                    progress,
                    Decimal(0),
                    f"{station_id}:national-road",
                )
            )
        return tuple(proposal)

    def _select_graph(
        self,
        possible: tuple[StationCandidate, ...],
        start: Coordinates,
        finish: Coordinates,
        initial: Decimal,
        reserve: Decimal,
        budget: RequestBudget,
        verification_reserve: float,
        baseline: Route | None,
        seed_ids: tuple[str, ...],
        incumbent_route: Route | None,
        max_total_seconds: Decimal,
        max_total_meters: Decimal | None,
    ) -> QueryRoadSelection | NationalRoadSelection | None:
        """Choose measured completed rows or a bounded pending-source query."""
        selected: QueryRoadSelection | NationalRoadSelection | None = None
        try:
            if self.allow_partial:
                completed = completed_road_points(
                    self.database,
                    self.candidates.version,
                    self.local.graph_version,
                    active_point_hash=self.candidates.road_point_hash,
                )
                pending = tuple(s for s in possible if s.coordinates not in completed)
                if baseline is not None and pending:
                    with (
                        discovery_context(budget, "local"),
                        budget.stage("local_query_discovery"),
                    ):
                        allowed = {s.station_id for s in possible}
                        along = self.candidates.along_route(baseline, 80_000)
                        envelope = self.candidates.within_detour_envelope(
                            baseline,
                            max(
                                baseline.distance_meters,
                                max_total_meters or max_total_seconds * 100,
                            ),
                        )
                        alternatives = tuple(
                            v
                            for v in (*along, *envelope)
                            if v.station.station_id in allowed
                        )
                    selected = build_query_road_graph(
                        baseline,
                        alternatives,
                        seed_ids,
                        self.local,
                        self.endpoint_cache,
                        initial,
                        reserve,
                        budget,
                        verification_reserve,
                        incumbent_route=incumbent_route,
                    )
                    if selected is None:
                        return None
                    possible = selected.retained
                else:
                    possible = tuple(
                        station
                        for station in possible
                        if station.coordinates in completed
                    )
                    selected = None
            else:
                selected = None
            if not possible:
                return None
            selected = selected or build_trip_road_graph(
                self.database,
                possible,
                start,
                finish,
                self.local,
                budget,
                candidate_version=self.candidates.version,
                active_point_hash=self.candidates.road_point_hash,
                initial_gallons=initial,
                reserve_gallons=reserve,
                endpoint_cache=self.endpoint_cache,
                sparse=len(possible) > 200,
            )
        except (
            StationRoadGraphUnavailable,
            LocalValhallaUnavailable,
            sqlite3.Error,
        ) as error:
            logger.info(
                "national_road_unavailable reason=%s detail=%s cause=%s",
                type(error).__name__,
                str(error)[:200],
                str(error.__cause__)[:200] if error.__cause__ else "none",
            )
            return None
        return selected
