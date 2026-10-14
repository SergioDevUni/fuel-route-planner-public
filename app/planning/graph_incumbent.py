"""Construct graph incumbents without changing the exact search scope."""

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.fuel import VehicleSpec
from app.planning.fuel import optimize_fuel
from app.planning.fuel_graph import (
    DirectedFuelGraph,
    GraphFuelSolution,
    SparseDirectedFuelGraph,
)
from app.planning.graph_preparation import Outgoing
from app.planning.ranking import solution_rank

SolutionKey = tuple[Decimal, Decimal, int, tuple[str, ...]]
SeedEntry = tuple[Decimal, Decimal, int, tuple[int, ...], tuple[Decimal, ...]]
Edges = tuple[dict[int, tuple[Decimal, Decimal]], ...]


@dataclass
class IncumbentBuilder:
    graph: DirectedFuelGraph | SparseDirectedFuelGraph
    outgoing: Outgoing
    initial: Decimal
    reserve: Decimal
    vehicle: VehicleSpec
    max_meters: Decimal | None
    max_seconds: Decimal | None
    stop_when: Callable[[], bool] | None
    on_improvement: Callable[[GraphFuelSolution], None] | None
    best: GraphFuelSolution | None = None
    best_key: SolutionKey | None = None
    by_id: dict[str, int] = field(init=False)
    edges: Edges = field(init=False)

    def __post_init__(self) -> None:
        self.by_id = {
            station.station_id: index
            for index, station in enumerate(self.graph.stations, 1)
        }
        self.edges = tuple(
            {target: (distance, seconds) for target, distance, seconds in row}
            for row in self.outgoing
        )

    @property
    def destination(self) -> int:
        return len(self.graph.stations) + 1

    def interrupted(self) -> bool:
        return self.stop_when is not None and self.stop_when()

    def permitted(self, meters: Decimal, seconds: Decimal) -> bool:
        return (self.max_meters is None or meters <= self.max_meters) and (
            self.max_seconds is None or seconds <= self.max_seconds
        )

    def evaluate(
        self, path: tuple[int, ...], legs: tuple[Decimal, ...], meters: Decimal
    ) -> tuple[GraphFuelSolution, SolutionKey]:
        stops = tuple(self.graph.stations[index - 1] for index in path)
        fuel = optimize_fuel(stops, legs, self.initial, self.reserve, self.vehicle)
        ids = tuple(stop.station_id for stop in stops)
        return (
            GraphFuelSolution(ids, legs, fuel, meters),
            solution_rank(fuel.exact_purchase_cost, meters, ids),
        )

    def retain(self, solution: GraphFuelSolution, key: SolutionKey) -> None:
        if self.best_key is None or key < self.best_key:
            self.best, self.best_key = solution, key

    def publish(self) -> None:
        if self.best is not None and self.on_improvement is not None:
            self.on_improvement(self.best)

    def supplied_seed(self, ids: tuple[str, ...] | None) -> None:
        # Projected seeds must be re-evaluated on actual directed road legs.
        if (
            ids is None
            or len(set(ids)) != len(ids)
            or any(s not in self.by_id for s in ids)
        ):
            return
        path = tuple(self.by_id[s] for s in ids)
        nodes = (0, *path, self.destination)
        legs = []
        for source, target in zip(nodes, nodes[1:], strict=False):
            leg = self.edges[source].get(target)
            if leg is None:
                return
            legs.append(leg)
        meters = sum((leg[0] for leg in legs), Decimal(0))
        seconds = sum((leg[1] for leg in legs), Decimal(0))
        if self.permitted(meters, seconds):
            self.retain(*self.evaluate(path, tuple(leg[0] for leg in legs), meters))

    def fastest_seed(self) -> None:
        # This single-label chain supplies only an upper bound. Exact resource
        # labels remain responsible for station selection and cost_search.
        pending: list[SeedEntry] = [(Decimal(0), Decimal(0), 0, (), ())]
        seen: set[int] = set()
        while pending:
            seconds, meters, current, path, legs = heapq.heappop(pending)
            if current in seen:
                continue
            seen.add(current)
            if current == self.destination:
                self.retain(*self.evaluate(path, legs, meters))
                return
            available = (
                self.initial if current == 0 else self.vehicle.capacity_gallons
            ) - self.reserve
            for target, leg, duration in self.outgoing[current]:
                if target == 0 or target in seen:
                    continue
                if self.vehicle.gallons_for_meters(leg) > available:
                    continue
                if not self.permitted(meters + leg, seconds + duration):
                    continue
                heapq.heappush(
                    pending,
                    (
                        seconds + duration,
                        meters + leg,
                        target,
                        path + (target,) if target != self.destination else path,
                        legs + (leg,),
                    ),
                )

    def insertion(
        self,
        nodes: tuple[int, ...],
        position: int,
        inserted: int,
        seconds_used: Decimal,
    ) -> tuple[GraphFuelSolution, SolutionKey] | None:
        assert self.best is not None
        source, target = nodes[position : position + 2]
        first, last = self.edges[source].get(inserted), self.edges[inserted].get(target)
        if first is None or last is None:
            return None
        original = self.edges[source][target]
        meters = self.best.total_distance_meters - original[0] + first[0] + last[0]
        seconds = seconds_used - original[1] + first[1] + last[1]
        if not self.permitted(meters, seconds):
            return None
        path = (*nodes[1 : position + 1], inserted, *nodes[position + 1 : -1])
        legs = (
            *self.best.leg_distances_meters[:position],
            first[0],
            last[0],
            *self.best.leg_distances_meters[position + 1 :],
        )
        return self.evaluate(path, legs, meters)

    def improve_insertions(self) -> None:
        # Insertions improve only the incumbent; no unexplored path is excluded.
        for _ in range(8):
            if self.best is None:
                return
            nodes = (
                0,
                *(self.by_id[s] for s in self.best.station_ids),
                self.destination,
            )
            used = set(nodes)
            seconds = sum(
                (self.edges[a][b][1] for a, b in zip(nodes, nodes[1:], strict=False)),
                Decimal(0),
            )
            improvement: GraphFuelSolution | None = None
            improvement_key = self.best_key
            interrupted = False
            for position in range(len(nodes) - 1):
                for inserted in range(1, self.destination):
                    if self.interrupted():
                        interrupted = True
                        break
                    if inserted in used:
                        continue
                    candidate = self.insertion(nodes, position, inserted, seconds)
                    if candidate is not None and (
                        improvement_key is None or candidate[1] < improvement_key
                    ):
                        improvement, improvement_key = candidate
                if interrupted:
                    break
            if improvement is not None:
                assert improvement_key is not None
                self.retain(improvement, improvement_key)
                self.publish()
            if improvement is None or interrupted:
                return


def initialize_incumbent(
    graph: DirectedFuelGraph | SparseDirectedFuelGraph,
    outgoing: Outgoing,
    initial_gallons: Decimal,
    reserve_gallons: Decimal,
    vehicle: VehicleSpec,
    max_total_meters: Decimal | None,
    max_total_seconds: Decimal | None,
    seed_station_ids: tuple[str, ...] | None,
    stop_when: Callable[[], bool] | None,
    on_improvement: Callable[[GraphFuelSolution], None] | None,
) -> tuple[GraphFuelSolution | None, SolutionKey | None]:
    builder = IncumbentBuilder(
        graph,
        outgoing,
        initial_gallons,
        reserve_gallons,
        vehicle,
        max_total_meters,
        max_total_seconds,
        stop_when,
        on_improvement,
    )
    builder.supplied_seed(seed_station_ids)
    builder.fastest_seed()
    builder.publish()
    builder.improve_insertions()
    return builder.best, builder.best_key
