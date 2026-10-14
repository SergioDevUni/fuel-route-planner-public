"""Run the real planner on captured roads; unknown queries fail without network."""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.api.serialization import serialize_trip
from app.api.validation import TripInput
from app.domain.location import ResolvedLocation
from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.candidates import CandidateIndex
from app.planning.trip import TripPlanner
from app.routing.client import RoutingUnavailable
from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.road_replay import decode_route


class FrozenRoads:
    def __init__(self, facts: dict, name: str, index: CandidateIndex):
        self.routes = {
            tuple(Coordinates(**point) for point in entry["points"]): decode_route(
                entry["route"]
            )
            for entry in facts["routes"][name]
        }
        self.graph = facts.get("graphs", {}).get(name)
        self.positions = {}
        if self.graph:
            direct = next(points for points in self.routes if len(points) == 2)
            by_id = {
                station.station_id: station.coordinates for station in index.stations
            }
            points = [
                direct[0],
                *(by_id[v["station_id"]] for v in self.graph["stations"]),
                direct[-1],
            ]
            self.positions = {point: position for position, point in enumerate(points)}

    def route(self, points, budget):
        budget.consume_attempt()
        if points not in self.routes:
            budget.increment_metric("road_replay_missing", 1)
            raise RoutingUnavailable("captured directions query is unavailable")
        return self.routes[points]

    def matrix(self, points, budget):
        budget.consume_attempt()
        if not self.graph or any(point not in self.positions for point in points):
            budget.increment_metric("road_replay_missing", 1)
            raise RoutingUnavailable("captured matrix query is unavailable")
        positions = [self.positions[p] for p in points]

        def rows(key):
            return tuple(
                tuple(
                    Decimal(self.graph[key][i][j])
                    if self.graph[key][i][j] is not None
                    else None
                    for j in positions
                )
                for i in positions
            )

        # Captured normalized graphs passed access checks; raw snaps were not
        # retained. Use conservative policy limits rather than invented samples.
        snaps = tuple(
            Decimal(1000 if i in (0, len(points) - 1) else 250)
            for i in range(len(points))
        )
        return RoadDistanceMatrix(
            rows("distances_meters"),
            snaps,
            snaps,
            rows("durations_seconds"),
        )


@dataclass
class ReplayCase:
    trip: TripInput
    start: ResolvedLocation
    finish: ResolvedLocation
    roads: FrozenRoads


def load_replays(
    root: Path, facts_path: Path
) -> tuple[CandidateIndex, dict[str, ReplayCase]]:
    # Resolve installed inputs once before timing; only the benchmark owns Django setup.
    from app.api.inputs import resolve_inputs

    facts = json.loads(facts_path.read_text())
    manifest = json.loads((root / "var/station_candidates/manifest.json").read_text())
    index = CandidateIndex(root / "var/station_candidates", manifest["source_sha256"])
    cases = {}
    for name in facts["routes"]:
        case = BENCHMARK_CASES[name]
        trip = TripInput(
            case.start,
            case.finish,
            Decimal(case.initial),
            Decimal("3.20"),
            Decimal(case.reserve),
            None,
            None,
        )
        start, finish = resolve_inputs(trip)
        cases[name] = ReplayCase(trip, start, finish, FrozenRoads(facts, name, index))
    return index, cases


def calculate_replay(
    case: ReplayCase, index: CandidateIndex, budget: RequestBudget
) -> dict:
    with budget.stage("offline_planner_replay"):
        trip = case.trip

        def publish(plan):
            payload = serialize_trip(trip, case.start, case.finish, plan)
            ids = tuple(station.station_id for station in plan.routed_stations)
            budget.publish_verified_payload(
                payload,
                (
                    plan.fuel.exact_purchase_cost,
                    plan.verified.distance_meters,
                    len(ids),
                    ids,
                ),
            )

        result = TripPlanner(
            case.roads, index, matrix_provider=case.roads, on_verified=publish
        ).plan(
            case.start.coordinates,
            case.finish.coordinates,
            trip.initial_fuel_gallons,
            trip.reserve_gallons,
            trip.max_detour_miles,
            trip.max_detour_ratio,
            trip.initial_fuel_price_per_gallon,
            trip.optimization_mode,
            trip.max_extra_duration_minutes,
            matrix_first=True,
            budget=budget,
        )
        return serialize_trip(trip, case.start, case.finish, result)
