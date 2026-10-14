"""Simulate per-trip CSV cost cost_review before the road cache is complete.

The small graph is deliberately synthetic: it checks solver behavior as road
sources, prices, and travel times change. The live audit reads the current
checkpoint and recent API incumbents without making routing requests.
"""

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.domain.fuel import FuelOpportunity  # noqa: E402
from app.domain.routing import Coordinates  # noqa: E402
from app.domain.units import METERS_PER_MILE  # noqa: E402
from app.planning.candidates import CandidateIndex, StationCandidate  # noqa: E402
from app.planning.fuel_graph import DirectedFuelGraph  # noqa: E402
from app.planning.graph_search import search_fuel_graph  # noqa: E402
from app.planning.mip_search import search_fuel_graph_mip  # noqa: E402
from app.planning.national_graph import completed_road_points  # noqa: E402
from app.planning.national_scope import screen_csv_offers  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.benchmarking.cases import CASES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "var/road_nationwide/station_edges_v2_live.db"
GRAPH_VERSION = "geofabrik-us-20261009-valhalla-3.9.1-auto"
DEFAULT_REPORTS = (
    ROOT / "var/benchmarks/2026-10-11-nationwide-current-hosted.jsonl",
    ROOT / "var/benchmarks/2026-10-11-nationwide-current-portfolio.jsonl",
    ROOT / "var/benchmarks/2026-10-11-nationwide-contract-hosted.jsonl",
    ROOT / "var/benchmarks/2026-10-11-nationwide-contract-portfolio.jsonl",
)
DEFAULT_OUTPUT = ROOT / "var/benchmarks/2026-10-11-csv-cost-cost_review-simulation.json"


def _offer(station_id: str, longitude: int, price: str) -> StationCandidate:
    return StationCandidate(
        station_id,
        f"Simulated CSV {station_id}",
        "Simulated road access",
        "Simulated city",
        "IL",
        Coordinates(40, longitude),
        Decimal(price),
    )


def _graph(
    offers: tuple[StationCandidate, ...], *, slow_cheap_stop: bool = False
) -> DirectedFuelGraph:
    """Two alternate 250-mile refills; direct 500-mile leg needs more fuel."""
    size = len(offers) + 2
    distance: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
    duration: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
    for index in range(size):
        distance[index][index] = duration[index][index] = Decimal(0)
    distance[0][-1] = Decimal(500) * METERS_PER_MILE
    duration[0][-1] = Decimal(10_000)
    for index, offer in enumerate(offers, start=1):
        distance[0][index] = distance[index][-1] = Decimal(250) * METERS_PER_MILE
        duration[0][index] = Decimal(5_200)
        duration[index][-1] = (
            Decimal(8_000)
            if slow_cheap_stop and offer.station_id == "B"
            else Decimal(5_200)
        )
    return DirectedFuelGraph(
        tuple(
            FuelOpportunity(item.station_id, item.price_per_gallon) for item in offers
        ),
        tuple(tuple(row) for row in distance),
        "simulation-auto-v1",
        tuple(tuple(row) for row in duration),
    )


def _solve(
    name: str,
    offers: tuple[StationCandidate, ...],
    *,
    missing_relevant_ids: tuple[str, ...] = (),
    slow_cheap_stop: bool = False,
) -> dict:
    graph = _graph(offers, slow_cheap_stop=slow_cheap_stop)
    search = search_fuel_graph(
        graph, Decimal(25), max_total_seconds=Decimal(11_800), max_expansions=10_000
    )
    mip = search_fuel_graph_mip(
        graph, Decimal(25), max_total_seconds=Decimal(11_800), max_solver_seconds=3
    )
    search_cost = search.solution.purchase_cost if search.solution else None
    mip_cost = mip.solution.purchase_cost if mip.solution else None
    agree = (
        search_cost is not None
        and mip_cost is not None
        and abs(search_cost - mip_cost) <= Decimal("0.000001")
    )
    if not agree or not search.complete or not mip.complete:
        status = "solver_disagreement_or_incomplete"
    elif missing_relevant_ids:
        status = "best_on_completed_sources_missing_csv_candidate"
    else:
        status = "measured_for_declared_simulation_graph"
    return {
        "scenario": name,
        "status": status,
        "search_complete": search.complete,
        "mip_complete": mip.complete,
        "solvers_agree": agree,
        "search_purchase_cost": str(search_cost) if search_cost is not None else None,
        "mip_purchase_cost": str(mip_cost) if mip_cost is not None else None,
        "selected_station_ids": list(search.solution.station_ids)
        if search.solution
        else [],
        "missing_relevant_ids": list(missing_relevant_ids),
    }


def simulate() -> list[dict]:
    start = Coordinates(40, -90)
    finish = Coordinates(40, -82)
    expensive = _offer("A", -86, "4.00")
    cheap = _offer("B", -86, "2.00")
    far = _offer("C", -110, "1.00")
    scope = screen_csv_offers(
        (expensive, cheap, far), start, finish, Decimal(25), Decimal(0), Decimal(100)
    )
    if {item.station.station_id for item in scope.possible_offers} != {"A", "B"}:
        raise AssertionError("safe cost screen should retain both relevant CSV offers")
    scenarios = [
        _solve("cheap_source_missing", (expensive,), missing_relevant_ids=("B",)),
        _solve("cheap_source_completed", (expensive, cheap)),
        _solve("price_update_without_road_rebuild", (_offer("A", -86, "1.50"), cheap)),
        _solve(
            "cheap_stop_exceeds_30_minute_limit",
            (expensive, cheap),
            slow_cheap_stop=True,
        ),
    ]
    scenarios[0]["safe_excluded_far_csv_ids"] = ["C"]
    expected = ("100.00", "50.00", "37.500", "100.00")
    for scenario, cost in zip(scenarios, expected, strict=True):
        if Decimal(scenario["search_purchase_cost"]) != Decimal(cost):
            raise AssertionError(f"unexpected cost in {scenario['scenario']}")
        if not scenario["solvers_agree"]:
            raise AssertionError(
                f"independent solvers disagree in {scenario['scenario']}"
            )
    return scenarios


def audit_current_cache(reports: tuple[Path, ...]) -> list[dict]:
    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    completed = completed_road_points(
        DATABASE, index.version, GRAPH_VERSION, active_point_hash=index.road_point_hash
    )
    incumbents: dict[str, dict] = {}
    for report in reports:
        for line in report.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("http_status") != 200 or row.get("contract_ok") is False:
                continue
            previous = incumbents.get(row["case"])
            if previous is None or Decimal(row["purchase_cost"]) < Decimal(
                previous["purchase_cost"]
            ):
                incumbents[row["case"]] = row
    output = []
    for case, row in sorted(incumbents.items()):
        start_spec, finish_spec, initial_text = CASES[case]
        trip = TripInput(
            start_spec,
            finish_spec,
            Decimal(initial_text),
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
            Decimal(row["purchase_cost"]),
        )
        possible = tuple(item.station for item in scope.possible_offers)
        missing = tuple(item for item in possible if item.coordinates not in completed)
        selected_ids = set(row.get("station_ids", []))
        selected = tuple(
            item for item in index.stations if item.station_id in selected_ids
        )
        zero_purchase_comparison = Decimal(row["purchase_cost"]) == 0
        output.append(
            {
                "case": case,
                "incumbent_purchase_cost": row["purchase_cost"],
                "incumbent_mode": row["mode"],
                "total_us_csv_offers": scope.total_csv_offers,
                "safely_excluded_by_cost": scope.excluded_by_cost,
                "remaining_possible_offers": len(possible),
                "remaining_possible_road_points": len(
                    {item.coordinates for item in possible}
                ),
                "missing_possible_source_points": len(
                    {item.coordinates for item in missing}
                ),
                "selected_stops_with_completed_sources": sum(
                    item.coordinates in completed for item in selected
                ),
                "selected_stop_count": len(selected),
                "cost_review_stage": (
                    "zero_purchase_case_purchase_cost_zero"
                    if zero_purchase_comparison
                    else (
                        "source_rows_available_needs_graph_and_solver_check"
                        if not missing
                        else "missing_relevant_source_rows"
                    )
                ),
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="+", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    simulation = simulate()
    report = {
        "purpose": "per-trip CSV cheapest-plan cost_review simulation",
        "synthetic_scenarios": simulation,
        "current_cache_audit": audit_current_cache(tuple(args.reports)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
