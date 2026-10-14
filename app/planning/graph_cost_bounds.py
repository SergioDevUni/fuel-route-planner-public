from bisect import bisect_right
from collections.abc import Callable
from decimal import Decimal

from app.domain.fuel import VehicleSpec
from app.planning.fuel_graph import DirectedFuelGraph, SparseDirectedFuelGraph
from app.planning.fuel_labels import FuelLabel
from app.planning.graph_bounds import shortest_from_rows
from app.planning.graph_preparation import PreparedGraph


def make_cost_bound(
    graph: DirectedFuelGraph | SparseDirectedFuelGraph,
    prepared: PreparedGraph,
    vehicle: VehicleSpec,
    max_total_seconds: Decimal | None,
) -> Callable[[FuelLabel, Decimal], Decimal]:
    remaining_time, time_bounds = prepared.remaining_time, prepared.time_bounds
    sparse_time_rows = prepared.sparse_time_rows
    cheapest = min(
        (station.price_per_gallon for station in graph.stations),
        default=Decimal(0),
    )
    # Each node has a sorted time-to-station-and-finish threshold and its
    # cheapest price prefix. A bound lookup is logarithmic rather than
    # scanning every CSV offer on every search expansion.
    time_price_index: dict[int, tuple[tuple[Decimal, ...], tuple[Decimal, ...]]] = {}

    def future_prices(
        node: int,
    ) -> tuple[tuple[Decimal, ...], tuple[Decimal, ...]]:
        existing = time_price_index.get(node)
        if existing is not None:
            return existing
        assert max_total_seconds is not None
        if time_bounds is not None:
            to_nodes = time_bounds[node]
            assert remaining_time is not None
            to_finish = remaining_time
        else:
            assert sparse_time_rows is not None and remaining_time is not None
            to_nodes = shortest_from_rows(sparse_time_rows, node, max_total_seconds)
            to_finish = remaining_time
        choices = sorted(
            (to_station + finish_time, station.price_per_gallon)
            for index, station in enumerate(graph.stations, 1)
            if (to_station := to_nodes[index]) is not None
            and (finish_time := to_finish[index]) is not None
        )
        thresholds: list[Decimal] = []
        prices: list[Decimal] = []
        lowest: Decimal | None = None
        for threshold, price in choices:
            thresholds.append(threshold)
            lowest = price if lowest is None else min(lowest, price)
            prices.append(lowest)
        index = (tuple(thresholds), tuple(prices))
        time_price_index[node] = index
        return index

    def cost_lower_bound(label: FuelLabel, suffix_distance: Decimal) -> Decimal:
        optimistic_price = cheapest
        if time_bounds is not None or (
            sparse_time_rows is not None
            and len(graph.stations) <= 500
            and max_total_seconds is not None
        ):
            assert max_total_seconds is not None
            thresholds, prices = future_prices(label.node)
            position = bisect_right(thresholds, max_total_seconds - label.elapsed) - 1
            if position >= 0:
                optimistic_price = prices[position]
        # Previously purchased cheap fuel is finite: the profile already accounts
        # for it. Past station prices must not fund unlimited future purchases.
        # Decimal operations can differ by a last digit from fixed-route quantity
        # reconstruction. A tiny downward guard keeps this a pruning lower bound.
        return max(
            Decimal(0),
            label.profile.completion_bound(
                vehicle.gallons_for_meters(suffix_distance), optimistic_price
            )
            - Decimal("1e-18"),
        )

    return cost_lower_bound
