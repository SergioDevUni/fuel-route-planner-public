"""CLI for nationwide quality measurements with explicit initialization."""

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import django

from scripts.benchmarking.capture import HOSTED_GRAPHS
from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.fingerprints import benchmark_sources
from scripts.benchmarking.policy import GRAPH_VERSION
from scripts.check_pilot_routes import routing_key
from scripts.local_common import ROOT
from scripts.nationwide_quality_metrics import cost_gap, summarize


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    from app.planning.candidates import CandidateIndex
    from app.stations.models import DatasetVersion
    from scripts.benchmarking.measurement import ProviderPacing, measure
    from scripts.benchmarking.references import offline_reference

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=tuple(BENCHMARK_CASES),
        default=list(BENCHMARK_CASES),
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("hosted_default", "portfolio"),
        default=["hosted_default", "portfolio"],
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--url", default="http://127.0.0.1:8005")
    parser.add_argument("--reference-seconds", type=float, default=60)
    parser.add_argument("--mip-seconds", type=float, default=5)
    parser.add_argument("--skip-references", action="store_true")
    parser.add_argument("--skip-local-references", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "var/benchmarks/2026-10-11-nationwide-quality.json",
    )
    args = parser.parse_args()
    if args.repeat < 1 or args.reference_seconds < 10 or args.mip_seconds <= 0:
        parser.error("positive repeats and valid reference budgets are required")
    os.environ.update(
        {
            "ORS_API_KEY": routing_key(),
            "FUEL_NATIONAL_LOCAL_ROUTER": "0",
            "FUEL_PARTIAL_ROAD_LOCAL_FIRST": "0",
            "FUEL_MATRIX_GRAPH": "1",
            "FUEL_MATRIX_FIRST": "1",
            "FUEL_NATIONAL_VALHALLA_URL": args.url,
            "FUEL_NATIONAL_VALHALLA_VERSION": GRAPH_VERSION,
        }
    )
    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    run_version = "quality-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    report = {
        "created_utc": datetime.now(UTC).isoformat(),
        "benchmark_source_sha256": benchmark_sources(ROOT),
        "candidate_version": index.version,
        "price_source_sha256": active.source_sha256,
        "cases": {name: asdict(BENCHMARK_CASES[name]) for name in args.cases},
        "online_planning_ceiling_seconds": 15,
        "online_target_seconds": 10,
        "online_target_is_acceptance_gate": False,
        "provider_cache_state": (
            "unknown; hosted local baseline/matrix/result caches controlled; "
            "local endpoint blocks retain their existing state"
        ),
        "process_state": (
            "warm Django in-process HTTP client; excludes network/server queue"
        ),
        "builder_state": "unchanged; no pause requested by benchmark",
        "benchmark_pacing": (
            "at most 28 counted attempts per 61 seconds; waits outside API timer"
        ),
        "offline_reference_seconds": args.reference_seconds,
        "independent_mip_seconds": args.mip_seconds,
        "local_references_enabled": not args.skip_local_references,
        "rows": [],
        "references": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pacing = ProviderPacing()

    def checkpoint() -> None:
        args.output.write_text(
            json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
        )

    for name in args.cases:
        for mode in args.modes:
            for trial in range(1, args.repeat + 1):
                for state in ("routing_cold", "routing_warm", "result_cached"):
                    pacing.before_request()
                    row = measure(name, mode, state, trial, index, run_version)
                    pacing.record(row["hosted_attempts"] or 0)
                    report["rows"].append(row)
                    checkpoint()
                    print(json.dumps(row), flush=True)
    if not args.skip_references:
        for name in args.cases:
            pacing.before_request()
            reference = offline_reference(name, report["rows"], index, args, pacing)
            report["references"].append(reference)
            checkpoint()
            print(json.dumps({"reference": reference}), flush=True)
    references = {reference["case"]: reference for reference in report["references"]}
    report["captured_hosted_graphs"] = {
        name: asdict(graph) for name, graph in HOSTED_GRAPHS.items()
    }
    for row in report["rows"]:
        reference = references.get(row["case"])
        if (
            row["contract_ok"]
            and row["profile"] == "ors_fastest_driving_car"
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
        for mode in args.modes
        for state in ("routing_cold", "routing_warm", "result_cached")
    }
    checkpoint()
    print(
        json.dumps({"output": str(args.output), "summary": report["summary"]}),
        flush=True,
    )


if __name__ == "__main__":
    main()
