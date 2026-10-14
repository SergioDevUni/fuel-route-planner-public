"""Performance campaign metadata and summaries, separate from HTTP execution."""

import hashlib
import os
import platform
from datetime import UTC, datetime

from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.fingerprints import benchmark_sources
from scripts.local_common import ROOT, planner_hash
from scripts.performance_metrics import comparison_summary, http_summary


def initial_report(names, manifest, descriptor, index, args):
    from app.api.inputs import resolve_inputs
    from scripts.benchmarking.measurement import trip_input

    cases = {}
    states = set()
    for name in names:
        case = BENCHMARK_CASES[name]
        start, finish = resolve_inputs(trip_input(case))
        states.update((start.state, finish.state))
        cases[name] = {
            "start": case.start,
            "finish": case.finish,
            "start_name": start.name,
            "finish_name": finish.name,
            "start_state": start.state,
            "finish_state": finish.state,
            "initial_fuel_gallons": case.initial,
            "reserve_gallons": case.reserve,
        }
    report = {
        "schema": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "release": manifest["version"],
        "planner_sha256": planner_hash(ROOT),
        "asset_sha256": descriptor["sha256"],
        "candidate_sha256": index.version,
        "road": descriptor["road"],
        "source_sha256": descriptor["dataset"]["source_sha256"],
        "benchmark_script_sha256": hashlib.sha256(
            (ROOT / "scripts/benchmark_release_performance.py")
            .read_bytes()
            .replace(b"\r\n", b"\n")
        ).hexdigest(),
        "benchmark_source_sha256": benchmark_sources(ROOT),
        "environment": {
            "transport": "live HTTP to Waitress on loopback",
            "routing": "live openrouteservice fastest driving-car",
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "logical_processors": os.cpu_count(),
            "waitress_threads": 12,
            "planning_capacity": descriptor["hosted_settings"][
                "FUEL_PLANNING_CAPACITY"
            ],
            "background_work": (
                "Existing host work remains active; the installed road checkpoint "
                "stays frozen."
            ),
        },
        "protocol": {
            "concurrency_levels": args.concurrency,
            "repeats": args.repeats,
            "routing_cold": (
                "Clear local baseline, projection, incumbent and result caches; "
                "use a new matrix-cache namespace. Prepared assets stay loaded."
            ),
            "routing_warm": (
                "Retain routing and incumbent caches; clear the result cache."
            ),
            "result_cached": "Repeat the same request with its result cache retained.",
            "provider_cache": "Provider-side cache state is uncontrolled.",
            "timing": (
                "Client wall time includes HTTP, server queue, planning, and "
                "persistence. Provider pacing and batch preparation occur outside "
                "the timer."
            ),
            "percentile": (
                "Linear interpolation of sorted observations; all-response and "
                "successful-plan values are separate."
            ),
            "reference": (
                "Independent SciPy/HiGHS MIP on captured matrices and independent "
                "price/access discovery batches. Cross-batch edges and nationwide "
                "omissions remain unknown. Final itineraries use verified road "
                "directions from the same routing profile."
            ),
            "refill_baseline": (
                "On the independent reference itinerary, fill at each station to "
                "tank capacity or remaining trip need plus reserve, whichever is "
                "smaller."
            ),
            "comparison_request": (
                "Use the first routing-cold trial of each case; retain all failed "
                "or missing-reference cases in the report."
            ),
        },
        "endpoint_states": sorted(states),
        "cases": cases,
        "rows": [],
        "references": [],
        "concurrent_rows": [],
        "batches": [],
    }
    return report


def summarize_report(report, concurrency_levels):
    report["sequential_summary"] = {
        state: http_summary(
            [row for row in report["rows"] if row["cache_state"] == state]
        )
        for state in ("routing_cold", "routing_warm", "result_cached")
    }
    report["class_summary"] = {
        f"{kind}/{state}": http_summary(
            [
                row
                for row in report["rows"]
                if row.get("trip_class") == kind and row["cache_state"] == state
            ]
        )
        for kind in ("short", "regional", "long", "unclassified")
        for state in ("routing_cold", "routing_warm", "result_cached")
    }
    report["comparison_summary"] = comparison_summary(report["references"])
    report["concurrent_summary"] = {}
    for concurrency in concurrency_levels:
        for state in ("routing_cold", "routing_warm", "result_cached"):
            rows = [
                row
                for row in report["concurrent_rows"]
                if row["concurrency"] == concurrency and row["cache_state"] == state
            ]
            batches = [
                batch
                for batch in report["batches"]
                if batch["concurrency"] == concurrency and batch["cache_state"] == state
            ]
            result = http_summary(rows)
            wall = sum(batch["seconds"] for batch in batches)
            result["requests_per_second_in_measured_batches"] = (
                round(len(rows) / wall, 3) if wall else None
            )
            report["concurrent_summary"][f"{concurrency}/{state}"] = result
    report["finished_utc"] = datetime.now(UTC).isoformat()
