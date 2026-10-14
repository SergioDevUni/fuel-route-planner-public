"""Fuel-search label extension and destination evaluation with explicit state."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.domain.fuel import VehicleSpec
from app.planning.fuel import optimize_fuel
from app.planning.fuel_graph import (
    DirectedFuelGraph,
    GraphFuelSolution,
    SparseDirectedFuelGraph,
)
from app.planning.fuel_labels import FuelLabel
from app.planning.graph_preparation import PreparedGraph
from app.planning.ranking import solution_rank

SolutionRank = tuple[Decimal, Decimal, int, tuple[str, ...]]


@dataclass
class SearchState:
    graph: DirectedFuelGraph | SparseDirectedFuelGraph
    prepared: PreparedGraph
    vehicle: VehicleSpec
    initial: Decimal
    reserve: Decimal
    max_meters: Decimal | None
    max_seconds: Decimal | None
    cost_bound: Callable[[FuelLabel, Decimal], Decimal]
    best: GraphFuelSolution | None
    best_key: SolutionRank | None
    on_improvement: Callable[[GraphFuelSolution], None] | None

    def exceeds_incumbent(self, cost: Decimal, distance: Decimal) -> bool:
        return self.best_key is not None and (
            cost > self.best_key[0]
            or (cost == self.best_key[0] and distance > self.best_key[1])
        )

    def available(self, node: int) -> Decimal:
        return (
            self.initial if node == 0 else self.vehicle.capacity_gallons
        ) - self.reserve

    def feasible_label(self, label: FuelLabel, distance_bound: Decimal) -> bool:
        if self.max_meters is not None and distance_bound > self.max_meters:
            return False
        remaining_time = self.prepared.remaining_time
        seconds = remaining_time[label.node] if remaining_time is not None else None
        return self.max_seconds is None or (
            seconds is not None and label.elapsed + seconds <= self.max_seconds
        )

    def evaluate_destination(self, label: FuelLabel) -> None:
        finish = self.prepared.finish_legs[label.node]
        if finish is None:
            return
        meters, seconds = finish
        if self.vehicle.gallons_for_meters(meters) > self.available(label.node):
            return
        total = label.traveled + meters
        if self.max_meters is not None and total > self.max_meters:
            return
        if self.max_seconds is not None and (
            seconds is None or label.elapsed + seconds > self.max_seconds
        ):
            return
        profile = label.profile.drive(self.vehicle.gallons_for_meters(meters))
        if profile is None or (
            self.best_key is not None
            and profile.points[0][1] > self.best_key[0] + Decimal("1e-18")
        ):
            return
        stops = tuple(self.graph.stations[index - 1] for index in label.path)
        legs = (*label.legs, meters)
        fuel = optimize_fuel(stops, legs, self.initial, self.reserve, self.vehicle)
        # Travel waypoints retain visits with zero purchases.
        ids = tuple(stop.station_id for stop in stops)
        key = solution_rank(fuel.exact_purchase_cost, total, ids)
        if self.best_key is None or key < self.best_key:
            self.best_key = key
            self.best = GraphFuelSolution(ids, legs, fuel, total)
            if self.on_improvement is not None:
                self.on_improvement(self.best)

    def extend(
        self, label: FuelLabel, target: int, meters: Decimal, seconds: Decimal
    ) -> tuple[FuelLabel, Decimal, Decimal] | None:
        if target == len(self.graph.stations) + 1 or target == 0:
            return None
        remaining = self.prepared.remaining[target]
        if (
            target == label.node
            or target in label.path
            or self.vehicle.gallons_for_meters(meters) > self.available(label.node)
            or remaining is None
        ):
            return None
        distance = label.traveled + meters
        distance_bound = distance + remaining
        if self.max_meters is not None and distance_bound > self.max_meters:
            return None
        times = self.prepared.remaining_time
        remaining_time = times[target] if times is not None else None
        if self.max_seconds is not None and (
            remaining_time is None
            or label.elapsed + seconds + remaining_time > self.max_seconds
        ):
            return None
        arrival = label.profile.drive(self.vehicle.gallons_for_meters(meters))
        if arrival is None:
            return None
        profile = arrival.buy(
            self.graph.stations[target - 1].price_per_gallon,
            self.vehicle.capacity_gallons - self.reserve,
        )
        ids = (*label.ids, self.graph.stations[target - 1].station_id)
        next_label = FuelLabel(
            target,
            (*label.path, target),
            (*label.legs, meters),
            distance,
            label.elapsed + seconds,
            profile,
            label.visited | (1 << target),
            ids,
        )
        cost = self.cost_bound(next_label, remaining)
        return (
            None
            if self.exceeds_incumbent(cost, distance_bound)
            else (next_label, cost, distance_bound)
        )
