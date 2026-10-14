"""Select offers, acquire directed road facts and account for unknown sources."""

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.fuel import FuelOpportunity, VehicleSpec
from app.domain.routing import Coordinates, Route
from app.domain.units import METERS_PER_MILE
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.candidates import ProjectedCandidate, StationCandidate
from app.planning.endpoint_block_cache import EndpointBlockCache
from app.planning.fuel_graph import SparseDirectedFuelGraph
from app.planning.matrix_candidates import matrix_points, select_matrix_candidates
from app.routing.geometry import geodesic_meters
from app.routing.local_valhalla import LocalValhalla, LocalValhallaUnavailable

RoadLeg = tuple[Decimal, Decimal]


@dataclass(frozen=True, slots=True)
class QueryRoadSelection:
    graph: SparseDirectedFuelGraph
    retained: tuple[StationCandidate, ...]
    unknown_ids: tuple[str, ...]
    unavailable_ids: tuple[str, ...] = ()


def build_query_road_graph(
    baseline: Route,
    alternatives: tuple[ProjectedCandidate, ...],
    seed_ids: tuple[str, ...],
    local: LocalValhalla,
    cache: EndpointBlockCache,
    initial: Decimal,
    reserve: Decimal,
    budget: RequestBudget,
    verification_reserve: float,
    *,
    incumbent_route: Route | None = None,
) -> QueryRoadSelection | None:
    by_id = {v.station.station_id: v for v in alternatives}
    chosen = select_matrix_candidates(
        tuple(by_id[s] for s in seed_ids if s in by_id),
        alternatives,
        baseline.distance_meters,
        initial_gallons=initial,
        reserve_gallons=reserve,
        offset_price_penalty_per_mile=budget.optimization.access_price_penalty,
    )
    if not chosen:
        return None
    builder = _QueryRoadBuilder(
        baseline,
        chosen,
        seed_ids,
        local,
        cache,
        initial,
        reserve,
        budget,
        verification_reserve,
    )
    builder.seed(incumbent_route)
    builder.acquire()
    return builder.result(alternatives)


@dataclass
class _QueryRoadBuilder:
    baseline: Route
    chosen: tuple[ProjectedCandidate, ...]
    seed_ids: tuple[str, ...]
    local: LocalValhalla
    cache: EndpointBlockCache
    initial: Decimal
    reserve: Decimal
    budget: RequestBudget
    verification_reserve: float
    points: tuple[Coordinates, ...] = field(init=False)
    offer_indices: tuple[int, ...] = field(init=False)
    nodes: dict[int, list[int]] = field(init=False)
    rows: list[dict[int, RoadLeg]] = field(init=False)
    seeded_points: tuple[int, ...] = field(init=False)
    known: set[tuple[int, int]] = field(default_factory=set)
    queried: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.points, self.offer_indices = matrix_points(
            self.baseline.waypoints[0].requested,
            self.chosen,
            self.baseline.waypoints[-1].requested,
        )
        final = len(self.chosen) + 1
        self.nodes = {0: [0], len(self.points) - 1: [final]}
        for node, point in enumerate(self.offer_indices, 1):
            self.nodes.setdefault(point, []).append(node)
        self.rows = [{} for _ in range(final + 1)]
        for nodes in self.nodes.values():
            for source in nodes:
                for target in nodes:
                    if source != target:
                        self.rows[source][target] = (Decimal(0), Decimal(0))
        by_id = {v.station.station_id: i for i, v in enumerate(self.chosen)}
        self.seeded_points = tuple(
            self.offer_indices[by_id[s]] for s in self.seed_ids if s in by_id
        )

    def usable_range(self, node: int) -> Decimal:
        vehicle = VehicleSpec()
        gallons = self.initial if node == 0 else vehicle.capacity_gallons
        return (gallons - self.reserve) * vehicle.miles_per_gallon * METERS_PER_MILE

    def add_leg(self, source: int, target: int, leg: RoadLeg) -> int:
        added = 0
        for source_node in self.nodes[source]:
            if leg[0] > self.usable_range(source_node):
                continue
            for target_node in self.nodes[target]:
                if source_node == target_node:
                    continue
                old = self.rows[source_node].get(target_node)
                if old is None or (leg[0] <= old[0] and leg[1] <= old[1]):
                    self.rows[source_node][target_node] = leg
                    added += 1
        return added

    def seed(self, route: Route | None) -> None:
        fastest = f"valhalla:{self.local.graph_version}:auto"
        if route is None or route.engine_version not in {fastest, fastest + ":shorter"}:
            return
        sequence = (0, *self.seeded_points, len(self.points) - 1)
        expected = tuple(self.points[i] for i in sequence)
        if (
            tuple(v.requested for v in route.waypoints) != expected
            or len(route.legs) != len(expected) - 1
        ):
            return
        for source, target, leg in zip(
            sequence, sequence[1:], route.legs, strict=False
        ):
            added = self.add_leg(
                source, target, (leg.distance_meters, leg.duration_seconds)
            )
            self.budget.increment_metric("local_verified_seed_edges", added)
            # Alternate-route legs remain proposals. Only the same fastest
            # profile can replace a directed matrix measurement.
            if route.engine_version == fastest:
                self.known.add((source, target))

    def interrupted(self) -> bool:
        return self.budget.cancelled or (
            self.budget.remaining_seconds <= self.verification_reserve + 0.3
        )

    def targets(self, source: int, successor: int | None) -> tuple[list[int], Decimal]:
        usable = max(self.usable_range(node) for node in self.nodes[source])
        targets = [
            i
            for i, point in enumerate(self.points)
            if i != source
            and max(Decimal(0), geodesic_meters(self.points[source], point) - 2000)
            <= usable + 1
        ]
        targets.sort(
            key=lambda i: (
                i != successor,
                i != len(self.points) - 1,
                min(
                    (
                        self.chosen[n - 1].station.price_per_gallon
                        for n in self.nodes.get(i, ())
                        if 0 < n < len(self.rows) - 1
                    ),
                    default=Decimal(0),
                ),
                i,
            )
        )
        unknown = [i for i in targets if (source, i) not in self.known]
        self.budget.increment_metric(
            "local_reused_road_cells", len(targets) - len(unknown)
        )
        return unknown, usable

    def acquire(self) -> None:
        sources = tuple(
            dict.fromkeys([0, *self.seeded_points, *range(1, len(self.points) - 1)])
        )
        successors = dict(
            zip(
                [0, *self.seeded_points],
                [*self.seeded_points, len(self.points) - 1],
                strict=True,
            )
        )
        for source in sources:
            if self.interrupted():
                break
            targets, usable = self.targets(source, successors.get(source))
            if self.acquire_source(source, targets, usable):
                self.queried.add(source)

    def acquire_source(self, source: int, targets: list[int], usable: Decimal) -> bool:
        complete = True
        for begin in range(0, len(targets), self.local.max_points):
            if self.interrupted():
                return False
            indices = tuple(targets[begin : begin + self.local.max_points])
            query_budget = self.budget.child(
                max(0.1, self.budget.remaining_seconds - self.verification_reserve)
            )
            try:
                with self.budget.stage("on_demand_road_queries"):
                    block = self.cache.matrix_block(
                        (self.points[source],),
                        tuple(self.points[i] for i in indices),
                        query_budget,
                    )
            except (PlanningDeadlineExceeded, LocalValhallaUnavailable):
                return False
            self.budget.increment_metric("local_queried_road_cells", len(indices))
            for column, target in enumerate(indices):
                meters = block.distances_meters[0][column]
                seconds = block.durations_seconds[0][column]
                if meters is None or seconds is None:
                    complete = False
                    self.budget.increment_metric("local_unknown_road_cells", 1)
                elif meters > usable:
                    self.budget.increment_metric("local_fuel_excluded_road_cells", 1)
                else:
                    self.add_leg(source, target, (meters, seconds))
        return complete

    def result(
        self, alternatives: tuple[ProjectedCandidate, ...]
    ) -> QueryRoadSelection:
        unknown = tuple(
            sorted(
                {v.station.station_id for v in alternatives}
                - {v.station.station_id for v in self.chosen}
                | {
                    v.station.station_id
                    for v, point in zip(self.chosen, self.offer_indices, strict=True)
                    if point not in self.queried
                }
            )
        )
        self.budget.audit_offers(
            "local_query_discovered", (v.station.station_id for v in alternatives)
        )
        self.budget.audit_offers(
            "local_query_selected", (v.station.station_id for v in self.chosen)
        )
        self.budget.audit_offers("local_query_unfinished", iter(unknown))
        graph = SparseDirectedFuelGraph(
            tuple(
                FuelOpportunity(v.station.station_id, v.station.price_per_gallon)
                for v in self.chosen
            ),
            tuple(
                tuple((target, *leg) for target, leg in sorted(row.items()))
                for row in self.rows
            ),
            f"on-demand:{self.local.graph_version}",
        )
        return QueryRoadSelection(graph, tuple(v.station for v in self.chosen), unknown)
