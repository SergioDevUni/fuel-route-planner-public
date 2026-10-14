"""Reverify saved offline candidates without assuming hidden route waypoints.

Preserve prior evidence and use the same road profile, fuel and time policy.
No online requests or solver solutions are fed back into production planning.
"""

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

from app.api.inputs import resolve_inputs
from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.measurement import (
    ROOT,
    CandidateIndex,
    DatasetVersion,
    OpenRouteServiceAdapter,
    ProviderPacing,
    RequestBudget,
    calculate_fuel,
    routing_key,
    trip_input,
    usd_cents,
)
from scripts.nationwide_quality_metrics import cost_gap, summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--historical", nargs="*", type=Path, default=[])
    parser.add_argument("--cases", nargs="+", choices=tuple(BENCHMARK_CASES))
    args = parser.parse_args()
    report = json.loads(args.source.read_text(encoding="utf-8"))
    prior_sequences = {
        row["case"]: row.get("verified_offline_candidates", [])
        for row in report.get("pre_reverification_references", [])
    }
    report["source_reference_report"] = str(args.source)
    os.environ["ORS_API_KEY"] = routing_key()
    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    if index.version != report["candidate_version"]:
        raise ValueError("reference dataset changed")
    by_id = {station.station_id: station for station in index.stations}
    historical = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.historical
    ]
    report["historical_comparison_files"] = [str(path) for path in args.historical]
    report["pre_reverification_references"] = report["references"]
    report["references"] = []
    report["reference_verification_policy"] = (
        "Each distinct offline sequence rerouted on ORS; no reuse of public "
        "purchase-only stop lists as a full waypoint-sequence comparison"
    )
    pacing = ProviderPacing()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint():
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for old in report["pre_reverification_references"]:
        name = old["case"]
        row = dict(old)
        if args.cases and name not in args.cases:
            report["references"].append(row)
            checkpoint()
            continue
        successes = [
            item
            for item in report["rows"]
            if item["case"] == name
            and item["contract_ok"]
            and item.get("profile") == "ors_fastest_driving_car"
        ]
        if not successes or old.get("zero_purchase_case"):
            report["references"].append(row)
            checkpoint()
            continue
        incumbent = min(successes, key=lambda item: Decimal(item["purchase_cost"]))
        row["reference_cost"] = incumbent["purchase_cost"]
        row["reference_kind"] = "best_observed_verified"
        row["independent_reference_available"] = False
        trip = trip_input(BENCHMARK_CASES[name])
        start, finish = resolve_inputs(trip)
        candidates = []
        verified = {}
        pacing.before_request()
        budget = RequestBudget(deadline_seconds=30)
        adapter = OpenRouteServiceAdapter(os.environ["ORS_API_KEY"])
        possible = list(
            prior_sequences.get(name, old.get("verified_offline_candidates", []))
        )
        historical_rows = []
        for previous in historical:
            if previous.get("candidate_version") != index.version:
                continue
            before = previous.get("cases", {}).get(name)
            current = report["cases"][name]
            if before is None or any(
                before.get(field) != current.get(field)
                for field in ("start", "finish", "initial", "reserve")
            ):
                continue
            historical_rows.extend(
                item
                for item in previous.get("rows", [])
                if item["case"] == name
                and item["contract_ok"]
                and item.get("profile") == "ors_fastest_driving_car"
            )
        if historical_rows:
            previous_best = min(
                historical_rows, key=lambda item: Decimal(item["purchase_cost"])
            )
            if Decimal(previous_best["purchase_cost"]) < Decimal(
                incumbent["purchase_cost"]
            ):
                possible.append(
                    {
                        "source": "historical_verified_sequence",
                        "valid": True,
                        "station_ids": previous_best["station_ids"],
                    }
                )
                row["historical_verified_cost"] = previous_best["purchase_cost"]
        try:
            direct = None
            for candidate in possible:
                if not candidate.get("valid"):
                    candidates.append(candidate)
                    continue
                ids = tuple(candidate["station_ids"])
                source = candidate["source"]
                try:
                    if ids not in verified:
                        if direct is None:
                            direct = adapter.route(
                                (start.coordinates, finish.coordinates), budget
                            )
                        stations = tuple(by_id[station_id] for station_id in ids)
                        route = adapter.route(
                            (
                                start.coordinates,
                                *(station.coordinates for station in stations),
                                finish.coordinates,
                            ),
                            budget,
                        )
                        if route.duration_seconds > direct.duration_seconds + 1800:
                            raise ValueError("hosted duration limit")
                        if any(
                            point.snap_distance_meters
                            > (
                                1000
                                if station.location_precision == "city_representative"
                                else 250
                            )
                            for point, station in zip(
                                route.waypoints[1:-1], stations, strict=True
                            )
                        ) or any(
                            point.snap_distance_meters > 1000
                            for point in (route.waypoints[0], route.waypoints[-1])
                        ):
                            raise ValueError("snap limit")
                        fuel = calculate_fuel(
                            stations,
                            tuple(leg.distance_meters for leg in route.legs),
                            trip.initial_fuel_gallons,
                            trip.reserve_gallons,
                        )
                        verified[ids] = usd_cents(fuel.exact_purchase_cost)
                    cost = verified[ids]
                    candidates.append(
                        {
                            "source": source,
                            "valid": True,
                            "cost": str(cost),
                            "station_ids": list(ids),
                            "full_sequence_hosted_verified": True,
                        }
                    )
                    if source.startswith("independent"):
                        row["independent_reference_available"] = True
                    if cost < Decimal(row["reference_cost"]):
                        row["reference_cost"], row["reference_kind"] = (
                            str(cost),
                            source + "_hosted_verified",
                        )
                except Exception as error:
                    candidates.append(
                        {
                            "source": source,
                            "valid": False,
                            "reason": type(error).__name__,
                        }
                    )
        finally:
            adapter.close()
            pacing.record(budget.attempts)
        row["reverification_hosted_attempts"] = budget.attempts
        row["verified_offline_candidates"] = candidates
        report["references"].append(row)
        checkpoint()
        print(
            json.dumps(
                {
                    "case": name,
                    "reference_cost": row.get("reference_cost"),
                    "independent": row.get("independent_reference_available"),
                    "candidates": candidates,
                }
            ),
            flush=True,
        )
    refs = {row["case"]: row for row in report["references"]}
    for row in report["rows"]:
        reference = refs.get(row["case"])
        if (
            row["contract_ok"]
            and row.get("profile") == "ors_fastest_driving_car"
            and reference
            and "reference_cost" in reference
        ):
            row["cost_gap"] = cost_gap(
                row["purchase_cost"], reference["reference_cost"]
            )
            row["reference_kind"] = reference["reference_kind"]
            row["independent_reference_available"] = reference[
                "independent_reference_available"
            ]
            row["zero_purchase_gap_percent"] = (
                row["cost_gap"]["percent"] if reference["zero_purchase_case"] else None
            )
    report["summary"] = {
        f"{mode}/{state}": summarize(
            [
                row
                for row in report["rows"]
                if row["mode"] == mode and row["cache_state"] == state
            ]
        )
        for mode in ("hosted_default", "portfolio")
        for state in ("routing_cold", "routing_warm", "result_cached")
    }
    report["held_out_summary"] = {
        f"{mode}/{state}": summarize(
            [
                row
                for row in report["rows"]
                if row["mode"] == mode
                and row["cache_state"] == state
                and row["held_out"]
            ]
        )
        for mode in ("hosted_default", "portfolio")
        for state in ("routing_cold", "routing_warm", "result_cached")
    }
    checkpoint()


if __name__ == "__main__":
    main()
