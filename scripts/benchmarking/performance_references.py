import json
import os
from decimal import Decimal
from types import SimpleNamespace

from app.api.inputs import resolve_inputs
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.matrix_candidates import matrix_points
from app.planning.road_graph import graph_from_matrix
from app.routing.client import OpenRouteServiceAdapter
from scripts.benchmarking.capture import HOSTED_GRAPHS
from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.hosted_reference import hosted_reference
from scripts.benchmarking.measurement import trip_input
from scripts.performance_metrics import percentage_saved, refill_reference
from scripts.reference_scope import merge_reference_graphs, reference_batches


def collect_references(
    names, report, index, routes, by_id, pacing, checkpoint, set_current_case
):
    for name in names:
        if any(row["case"] == name for row in report["references"]):
            continue
        set_current_case(name)
        seed = HOSTED_GRAPHS.get(name)
        graphs = [seed] if seed is not None else []
        discovery = {
            "method": "independent price/access batches",
            "all_csv_offers": len(index.stations),
            "batches": [],
            "missing_cross_batch_edges": True,
        }
        captured = routes.get(name, {})
        direct = next(
            (route for points, route in captured.items() if len(points) == 2),
            None,
        )
        online_seed = next(
            (
                row
                for row in report["rows"]
                if row["case"] == name and row["contract_ok"]
            ),
            None,
        )
        if (
            direct is not None
            and online_seed
            and Decimal(online_seed["purchase_cost"]) > 0
        ):
            alternatives = index.along_route(direct, 160_000)
            discovery["projected_offers"] = len(
                {v.station.station_id for v in alternatives}
            )
            adapter = OpenRouteServiceAdapter(os.environ["ORS_API_KEY"])
            try:
                for selected in reference_batches(
                    alternatives,
                    direct.distance_meters,
                    online_seed["station_ids"],
                ):
                    pacing.before_request()
                    points, indices = matrix_points(
                        direct.waypoints[0].requested,
                        selected,
                        direct.waypoints[-1].requested,
                    )
                    try:
                        matrix = adapter.matrix(
                            points, RequestBudget(deadline_seconds=30)
                        )
                        graph = graph_from_matrix(
                            tuple(v.station for v in selected),
                            matrix,
                            "independent-price-access",
                            station_point_indices=indices,
                        )
                        graphs.append(graph)
                        discovery["batches"].append(
                            {
                                "status": "measured",
                                "offers": len(graph.stations),
                            }
                        )
                    except (ValueError, RuntimeError) as error:
                        discovery["batches"].append({"status": type(error).__name__})
            finally:
                adapter.close()
        reference_graph = merge_reference_graphs(graphs) if graphs else None
        discovery["union_offers"] = (
            len(reference_graph.stations) if reference_graph else 0
        )
        pacing.before_request()
        raw = hosted_reference(
            name,
            report["rows"],
            index,
            SimpleNamespace(reference_seconds=15, mip_seconds=8),
            reference_graph=reference_graph,
        )
        pacing.record(raw.get("offline_hosted_attempts", 0))
        graph = HOSTED_GRAPHS.get(name)
        online = next(
            row
            for row in report["rows"]
            if row["case"] == name
            and row["cache_state"] == "routing_cold"
            and row["trial"] == 1
        )
        reference = {
            "case": name,
            "status": raw["status"],
            "online_valid": online["contract_ok"],
            "online_cost": online.get("purchase_cost"),
            "graph_offers": len(reference_graph.stations) if reference_graph else None,
            "solver_complete": raw.get("independent_solver_complete"),
            "hosted_attempts": raw.get("offline_hosted_attempts", 0),
            "candidates": raw.get("verified_offline_candidates", []),
            "discovery": discovery,
        }
        independent = next(
            (
                item
                for item in reference["candidates"]
                if item["source"] == "independent_same_profile_mip"
                and item.get("valid")
            ),
            None,
        )
        if independent and online["contract_ok"]:
            reference.update(
                reference_cost=independent["cost"],
                reference_station_ids=independent["station_ids"],
                status="verified_independent_mip",
                gap_dollars=str(
                    Decimal(online["purchase_cost"]) - Decimal(independent["cost"])
                ),
            )
            reference["gap_percent"] = (
                str(
                    Decimal(reference["gap_dollars"])
                    / Decimal(independent["cost"])
                    * 100
                )
                if Decimal(independent["cost"])
                else None
            )
            trip = trip_input(BENCHMARK_CASES[name])
            start, finish = resolve_inputs(trip)
            stations = tuple(by_id[value] for value in independent["station_ids"])
            points = (
                start.coordinates,
                *(station.coordinates for station in stations),
                finish.coordinates,
            )
            route = routes.get(name, {}).get(points)
            if route is not None:
                gallons = tuple(
                    leg.distance_meters / METERS_PER_MILE / 10 for leg in route.legs
                )
                baseline, ending = refill_reference(
                    trip.initial_fuel_gallons,
                    trip.reserve_gallons,
                    gallons,
                    tuple(station.price_per_gallon for station in stations),
                )
                reference.update(
                    refill_cost=str(baseline),
                    refill_ending_fuel=str(ending),
                    refill_savings_percent=percentage_saved(
                        online["purchase_cost"], str(baseline)
                    ),
                )
        elif online["contract_ok"] and Decimal(online["purchase_cost"]) == 0:
            reference.update(
                status="zero_purchase_case",
                reference_cost="0",
                gap_dollars="0",
                gap_percent=None,
            )
        report["references"].append(reference)
        checkpoint()
        print(
            json.dumps(
                {
                    "phase": "reference",
                    "case": name,
                    "status": reference["status"],
                    "gap_percent": reference.get("gap_percent"),
                    "refill_savings_percent": reference.get("refill_savings_percent"),
                }
            ),
            flush=True,
        )
