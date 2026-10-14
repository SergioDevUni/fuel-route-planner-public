"""Graph search for directed fuel planning."""

import heapq
from collections.abc import Callable
from decimal import Decimal

from app.domain.fuel import VehicleSpec
from app.planning.fuel_graph import (
    DirectedFuelGraph,
    GraphFuelSolution,
    GraphSearchResult,
    SparseDirectedFuelGraph,
)
from app.planning.fuel_labels import FuelLabel, HeapEntry, LabelFrontier
from app.planning.fuel_profile import FuelCostProfile
from app.planning.graph_cost_bounds import make_cost_bound
from app.planning.graph_incumbent import initialize_incumbent
from app.planning.graph_preparation import prepare_graph
from app.planning.graph_search_state import SearchState

SeedEntry = tuple[Decimal, Decimal, int, tuple[int, ...], tuple[Decimal, ...]]


def search_fuel_graph(
    graph: DirectedFuelGraph | SparseDirectedFuelGraph,
    initial_gallons: Decimal,
    reserve_gallons: Decimal = Decimal(0),
    *,
    max_total_meters: Decimal | None = None,
    max_total_seconds: Decimal | None = None,
    max_expansions: int = 100_000,
    vehicle: VehicleSpec | None = None,
    stop_when: Callable[[], bool] | None = None,
    seed_station_ids: tuple[str, ...] | None = None,
    on_improvement: Callable[[GraphFuelSolution], None] | None = None,
) -> GraphSearchResult:
    """Search simple paths; exact on a complete graph only if not capped."""
    vehicle = vehicle or VehicleSpec()
    prepared = prepare_graph(
        graph,
        initial_gallons,
        reserve_gallons,
        vehicle,
        max_total_meters,
        max_total_seconds,
        max_expansions,
    )
    outgoing, remaining = prepared.outgoing, prepared.remaining
    if remaining[0] is None:
        return GraphSearchResult(None, True, 0, graph.version)
    cost_lower_bound = make_cost_bound(graph, prepared, vehicle, max_total_seconds)
    origin = FuelLabel(
        0,
        (),
        (),
        Decimal(0),
        Decimal(0),
        FuelCostProfile.initial(initial_gallons - reserve_gallons),
        0,
        (),
    )
    heap: list[HeapEntry] = [
        (cost_lower_bound(origin, remaining[0]), remaining[0], (), 0, origin)
    ]
    frontiers: dict[int, LabelFrontier] = {0: LabelFrontier(origin)}
    serial = 0
    labels_pruned = 0
    best, best_key = initialize_incumbent(
        graph,
        outgoing,
        initial_gallons,
        reserve_gallons,
        vehicle,
        max_total_meters,
        max_total_seconds,
        seed_station_ids,
        stop_when,
        on_improvement,
    )
    state = SearchState(
        graph,
        prepared,
        vehicle,
        initial_gallons,
        reserve_gallons,
        max_total_meters,
        max_total_seconds,
        cost_lower_bound,
        best,
        best_key,
        on_improvement,
    )
    expansions = 0
    complete = True
    while heap:
        if stop_when is not None and stop_when():
            complete = False
            break
        if state.exceeds_incumbent(heap[0][0], heap[0][1]):
            break
        if expansions >= max_expansions:
            complete = False
            break
        cost_bound, distance_bound, _, _, label = heapq.heappop(heap)
        if not label.active:
            continue
        expansions += 1
        if state.exceeds_incumbent(cost_bound, distance_bound):
            continue
        if not state.feasible_label(label, distance_bound):
            continue
        state.evaluate_destination(label)
        for target, leg, leg_seconds in outgoing[label.node]:
            # Keep cancellation checks between expensive frontier comparisons.
            if stop_when is not None and stop_when():
                complete = False
                break
            extension = state.extend(label, target, leg, leg_seconds)
            if extension is None:
                continue
            next_label, cost_bound, distance_bound = extension
            frontier = frontiers.get(target)
            if frontier is None:
                frontier = LabelFrontier()
                frontiers[target] = frontier
            accepted, pruned = frontier.accept(next_label)
            labels_pruned += pruned
            if not accepted:
                continue
            serial += 1
            heapq.heappush(
                heap, (cost_bound, distance_bound, next_label.ids, serial, next_label)
            )
    return GraphSearchResult(
        state.best, complete, expansions, graph.version, labels_pruned
    )
