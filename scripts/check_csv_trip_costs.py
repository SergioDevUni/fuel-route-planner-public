"""Check trip-wide CSV screening and independent fuel solvers on live road data.

The timed graph/search/route phase models one local planning request. The MIP
is an offline cross-check and does not consume that request's 15-second budget.
"""

import argparse
import json
import os
import time
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.planning.budget import RequestBudget  # noqa: E402
from app.planning.candidates import CandidateIndex  # noqa: E402
from app.planning.endpoint_block_cache import EndpointBlockCache  # noqa: E402
from app.planning.fuel_graph import DirectedFuelGraph  # noqa: E402
from app.planning.graph_search import search_fuel_graph  # noqa: E402
from app.planning.mip_search import search_fuel_graph_mip  # noqa: E402
from app.planning.national_graph import (  # noqa: E402
    build_trip_road_graph,
    completed_road_points,
)
from app.planning.national_scope import screen_csv_offers  # noqa: E402
from app.planning.trip_results import calculate_fuel  # noqa: E402
from app.routing.local_valhalla import LocalValhalla  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.benchmarking.cases import CASES  # noqa: E402
from scripts.simulate_csv_cost_review import (  # noqa: E402
    DATABASE,
    DEFAULT_REPORTS,
    GRAPH_VERSION,
    ROOT,
)

DEFAULT_CASES = (
    "phoenix_tucson",
    "orlando_tampa",
    "atlanta_chattanooga",
    "albuquerque_denver",
)
DEFAULT_OUTPUT = ROOT / "var/benchmarks/2026-10-11-live-csv-trip-cost_search.json"


def _incumbents(reports: tuple[Path, ...]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for report in reports:
        for line in report.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("http_status") != 200 or row.get("contract_ok") is False:
                continue
            previous = best.get(row["case"])
            if previous is None or Decimal(row["purchase_cost"]) < Decimal(
                previous["purchase_cost"]
            ):
                best[row["case"]] = row
    return best


def check_case(
    case: str,
    incumbent: dict,
    index: CandidateIndex,
    completed: set,
    *,
    url: str,
    planning_seconds: float,
    mip_seconds: float,
) -> dict:
    start_spec, finish_spec, initial = CASES[case]
    trip = TripInput(
        start_spec,
        finish_spec,
        Decimal(initial),
        Decimal("3.20"),
        Decimal(0),
        None,
        None,
        "balanced",
        Decimal(30),
    )
    start, finish = resolve_inputs(trip)
    scope = screen_csv_offers(
        index.stations,
        start.coordinates,
        finish.coordinates,
        trip.initial_fuel_gallons,
        trip.reserve_gallons,
        Decimal(incumbent["purchase_cost"]),
        price_access_bound=True,
    )
    possible = tuple(item.station for item in scope.possible_offers)
    missing = tuple(item for item in possible if item.coordinates not in completed)
    result: dict = {
        "case": case,
        "incumbent_hosted_cost": incumbent["purchase_cost"],
        "us_csv_offers": scope.total_csv_offers,
        "safely_excluded_offers": scope.excluded_by_cost,
        "possible_offers": len(possible),
        "missing_source_points": len({item.coordinates for item in missing}),
        "planning_limit_seconds": planning_seconds,
        "mip_limit_seconds": mip_seconds,
    }
    if missing:
        result["status"] = "source_rows_needed"
        return result

    local = LocalValhalla(url, GRAPH_VERSION, request_timeout_seconds=10)
    budget = RequestBudget(deadline_seconds=planning_seconds)
    started = time.monotonic()
    try:
        baseline = local.route((start.coordinates, finish.coordinates), budget)
        selection = build_trip_road_graph(
            DATABASE,
            possible,
            start.coordinates,
            finish.coordinates,
            local,
            budget,
            candidate_version=index.version,
            active_point_hash=index.road_point_hash,
            initial_gallons=trip.initial_fuel_gallons,
            reserve_gallons=trip.reserve_gallons,
            endpoint_cache=EndpointBlockCache(
                DATABASE.parent / "endpoint_blocks", local
            ),
        )
        result["graph_seconds"] = round(time.monotonic() - started, 3)
        result["unavailable_csv_ids"] = list(selection.unavailable_ids)
        graph = selection.graph
        if not isinstance(graph, DirectedFuelGraph):
            raise TypeError("expected a dense graph for selected trips")
        max_seconds = baseline.duration_seconds + Decimal(1800)
        search = search_fuel_graph(
            graph,
            trip.initial_fuel_gallons,
            trip.reserve_gallons,
            max_total_seconds=max_seconds,
            max_expansions=100_000,
            stop_when=lambda: budget.remaining_seconds <= 2,
        )
        result["search_seconds"] = round(time.monotonic() - started, 3)
        result["search_complete"] = search.complete
        result["search_expansions"] = search.expansions
        result["search_cost"] = (
            str(search.solution.purchase_cost) if search.solution else None
        )
        if search.solution:
            by_id = {item.station_id: item for item in selection.retained}
            chosen = tuple(by_id[item] for item in search.solution.station_ids)
            route = local.route(
                (
                    start.coordinates,
                    *(item.coordinates for item in chosen),
                    finish.coordinates,
                ),
                budget,
            )
            fuel = calculate_fuel(
                chosen,
                tuple(leg.distance_meters for leg in route.legs),
                trip.initial_fuel_gallons,
                trip.reserve_gallons,
            )
            result["verified_station_ids"] = list(search.solution.station_ids)
            result["verified_cost"] = str(fuel.exact_purchase_cost)
            result["verified_extra_minutes"] = str(
                (route.duration_seconds - baseline.duration_seconds) / Decimal(60)
            )
            result["within_30_minutes"] = route.duration_seconds <= max_seconds
        result["planning_seconds"] = round(time.monotonic() - started, 3)

        mip = search_fuel_graph_mip(
            graph,
            trip.initial_fuel_gallons,
            trip.reserve_gallons,
            max_total_seconds=max_seconds,
            max_solver_seconds=mip_seconds,
        )
        result["mip_complete"] = mip.complete
        result["mip_cost"] = str(mip.solution.purchase_cost) if mip.solution else None
        result["mip_seconds"] = (
            round(time.monotonic() - started, 3) - result["planning_seconds"]
        )
        result["solvers_agree"] = (
            search.solution is not None
            and mip.solution is not None
            and abs(search.solution.purchase_cost - mip.solution.purchase_cost)
            <= Decimal("0.000001")
        )
        result["final_cost_delta"] = (
            str(Decimal(result["verified_cost"]) - search.solution.purchase_cost)
            if search.solution and "verified_cost" in result
            else None
        )
        result["status"] = (
            "local_graph_cost_measured"
            if not selection.unavailable_ids
            and search.complete
            and mip.complete
            and result["solvers_agree"]
            and result.get("within_30_minutes")
            and abs(Decimal(result["verified_cost"]) - search.solution.purchase_cost)
            <= Decimal("0.000001")
            and result["planning_seconds"] <= planning_seconds
            else (
                "unavailable_csv_road_access"
                if selection.unavailable_ids
                else "search_incomplete"
                if not search.complete
                else "independent_mip_incomplete"
                if not mip.complete
                else "verified_route_cost_differs_from_graph"
                if result["final_cost_delta"] is not None
                and Decimal(result["final_cost_delta"]) != 0
                else "needs_followup"
            )
        )
    except Exception as error:
        result["status"] = "error"
        result["error"] = type(error).__name__
        result["reason"] = str(error)
        result["planning_seconds"] = round(time.monotonic() - started, 3)
    finally:
        local.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", default=DEFAULT_CASES)
    parser.add_argument("--reports", nargs="+", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--url", default="http://127.0.0.1:8005")
    parser.add_argument("--planning-seconds", type=float, default=15)
    parser.add_argument("--mip-seconds", type=float, default=20)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    completed = completed_road_points(
        DATABASE, index.version, GRAPH_VERSION, active_point_hash=index.road_point_hash
    )
    best = _incumbents(tuple(args.reports))
    results = [
        check_case(
            case,
            best[case],
            index,
            completed,
            url=args.url,
            planning_seconds=args.planning_seconds,
            mip_seconds=args.mip_seconds,
        )
        for case in args.cases
    ]
    report = {"completed_source_points": len(completed), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
