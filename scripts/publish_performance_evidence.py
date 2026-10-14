"""Check a completed release benchmark and publish its measured evidence."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.local_common import ROOT, planner_hash
from scripts.performance_metrics import comparison_summary, http_summary

STATES = ("routing_cold", "routing_warm", "result_cached")
LABELS = {
    "routing_cold": "Routing cold",
    "routing_warm": "Routing warm",
    "result_cached": "Repeated request",
}
START = "<!-- performance-evidence:start -->"
END = "<!-- performance-evidence:end -->"


def check_public_fields(value) -> None:
    if isinstance(value, dict):
        private = {"authorization", "ors_api_key", "api_key", "result_token", "map_url"}
        if private.intersection(str(key).lower() for key in value):
            raise ValueError("public evidence contains a private field")
        for item in value.values():
            check_public_fields(item)
    elif isinstance(value, list):
        for item in value:
            check_public_fields(item)
    elif isinstance(value, str) and ("Bearer " in value or "#token=" in value):
        raise ValueError("public evidence contains a private access value")


def repeat_observations(rows: list[dict]) -> dict:
    successful = [row for row in rows if row["contract_ok"]]
    instrumented = [row for row in successful if "stage_seconds" in row]
    recalculated = sum("plan_execution" in row["stage_seconds"] for row in instrumented)
    return {
        "requests": len(rows),
        "successful_completed_result_reuses": len(instrumented) - recalculated,
        "successful_recalculations": recalculated,
        "unsuccessful_requests": len(rows) - len(successful),
        "uninstrumented_successes": len(successful) - len(instrumented),
        "method": "A reused completed result has no plan_execution stage.",
    }


def summarize_report(report: dict) -> dict:
    """Recompute counts from observations, including errors and missing references."""
    if not report.get("finished_utc"):
        raise ValueError("benchmark has not completed")
    names = set(report["cases"])
    levels = report["protocol"].get("concurrency_levels", [1, 3, 6])
    if (
        not levels
        or len(set(levels)) != len(levels)
        or any(level not in (1, 3, 6) for level in levels)
    ):
        raise ValueError("invalid declared concurrency levels")
    repeats = report["protocol"]["repeats"]
    expected = Counter(
        (name, state, trial)
        for name in names
        for state in STATES
        for trial in range(1, repeats + 1)
    )
    observed = Counter(
        (row["case"], row["cache_state"], row["trial"]) for row in report["rows"]
    )
    if not names or observed != expected:
        raise ValueError("sequential observations are missing or duplicated")
    if Counter(row["case"] for row in report["references"]) != Counter(names):
        raise ValueError("each case must have one reference outcome")
    concurrent = report["concurrent_rows"]
    load_names = {row["case"] for row in concurrent}
    expected_load = Counter(
        (name, state, users)
        for name in load_names
        for state in STATES
        for users in levels
    )
    if (
        not load_names
        or Counter(
            (row["case"], row["cache_state"], row["concurrency"]) for row in concurrent
        )
        != expected_load
    ):
        raise ValueError("concurrent observations are missing or duplicated")
    if not load_names <= names:
        raise ValueError("concurrent routes must belong to the measured sample")
    sequential_summary = {
        state: http_summary(
            [row for row in report["rows"] if row["cache_state"] == state]
        )
        for state in STATES
    }
    concurrent_summary = {}
    for users in levels:
        for state in STATES:
            rows = [
                row
                for row in concurrent
                if row["concurrency"] == users and row["cache_state"] == state
            ]
            batches = [
                batch
                for batch in report["batches"]
                if batch["concurrency"] == users and batch["cache_state"] == state
            ]
            batch_names = Counter(name for batch in batches for name in batch["cases"])
            if batch_names != Counter(load_names) or any(
                batch["requests"] != users or len(batch["cases"]) != users
                for batch in batches
            ):
                raise ValueError("concurrent batch records do not match observations")
            wall = sum(batch["seconds"] for batch in batches)
            result = http_summary(rows)
            result["requests_per_second_in_measured_batches"] = round(
                len(rows) / wall, 3
            )
            concurrent_summary[f"{users}/{state}"] = result
    references = report["references"]
    comparable = [
        row
        for row in references
        if row.get("refill_cost") is not None
        and Decimal(row["refill_cost"]) > 0
        and row.get("online_valid")
    ]
    refill_total = sum((Decimal(row["refill_cost"]) for row in comparable), Decimal(0))
    actual_total = sum((Decimal(row["online_cost"]) for row in comparable), Decimal(0))
    quality = comparison_summary(references)
    quality["aggregate_savings_against_refill_policy_percent"] = (
        round(float((refill_total - actual_total) / refill_total * 100), 3)
        if refill_total
        else None
    )
    quality["aggregate_refill_cost_dollars"] = str(refill_total)
    quality["aggregate_online_cost_dollars"] = str(actual_total)
    first_cold = [
        row
        for row in report["rows"]
        if row["cache_state"] == "routing_cold" and row["trial"] == 1
    ]
    class_summary = {
        f"{kind}/{state}": http_summary(
            [
                row
                for row in report["rows"]
                if row.get("trip_class", "unclassified") == kind
                and row["cache_state"] == state
            ]
        )
        for kind in ("short", "regional", "long", "unclassified")
        for state in STATES
    }
    first_classes = {
        row["case"]: row.get("trip_class", "unclassified") for row in first_cold
    }
    quality_by_class = {
        kind: comparison_summary(
            [row for row in references if first_classes[row["case"]] == kind]
        )
        for kind in ("short", "regional", "long", "unclassified")
    }
    return {
        "nationwide_sample": {
            "defined_routes": len(names),
            "endpoint_states": len(report["endpoint_states"]),
            "first_cold_trial": http_summary(first_cold),
            "all_requests": http_summary(report["rows"] + concurrent),
            "concurrent_routes": len(load_names),
        },
        "sequential_summary": sequential_summary,
        "class_summary": class_summary,
        "comparison_summary": quality,
        "comparison_by_trip_class": quality_by_class,
        "concurrent_summary": concurrent_summary,
        "result_repeat_observations": repeat_observations(
            [
                row
                for row in report["rows"] + concurrent
                if row["cache_state"] == "result_cached"
            ]
        ),
    }


def display(value: float | None, suffix: str = "") -> str:
    return f"{value:.3f}{suffix}" if value is not None else "No comparison"


def markdown(report: dict) -> str:
    sample = report["nationwide_sample"]
    first = sample["first_cold_trial"]
    quality = report["comparison_summary"]
    date = (
        datetime.fromisoformat(report["created_utc"])
        .astimezone(
            ZoneInfo(report.get("environment", {}).get("measurement_timezone", "UTC"))
        )
        .date()
    )
    lines = [
        START,
        "## Performance and cost evidence",
        "",
        f"On {date}, a fresh {report['release']} installation "
        f"ran {sample['defined_routes']} defined US routes with endpoints in "
        f"{sample['endpoint_states']} states.",
        "The sample includes short, regional, and long trips from the route catalog.",
        f"The first routing-cold trial produced {first['verified_successes']} verified "
        f"plans from {first['requests']} requests "
        f"({first['success_rate_percent']:.2f}% success).",
        "Each verified plan passed checks for CSV prices, fuel balances, the "
        "driving allowance, and saved-result retrieval.",
        "",
        f"Each route ran {report['protocol']['repeats']} times in each cache state "
        "through live Waitress HTTP and openrouteservice.",
        "",
        "| Cache state | Verified plans / requests | Success "
        "| p50, all | p95, all | p95, verified |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    annotation = (
        ROOT / "var/private-development/records/docs/evidence/performance-outcomes.json"
    )
    original = ROOT / "var/private-development/records/docs/evidence/performance.json"
    if annotation.exists() and original.exists():
        outcome_review = json.loads(annotation.read_text(encoding="utf-8"))
        if (
            outcome_review["original_evidence_sha256"]
            == hashlib.sha256(original.read_bytes()).hexdigest()
        ):
            summary = outcome_review["summary"]
            note = (
                f"A subsequent all-offer distance review classified "
                f"{summary['correct_infeasible_rejections']} response as a correct "
                "infeasible rejection. Read the "
                "[outcome review]"
                "(var/private-development/records/docs/evidence/performance-outcomes.json)."
            )
            position = lines.index(
                "Each route ran "
                f"{report['protocol']['repeats']} times in each cache state "
                "through live Waitress HTTP and openrouteservice."
            )
            lines[position:position] = [note, ""]
    for state in STATES:
        item = report["sequential_summary"][state]
        lines.append(
            f"| {LABELS[state]} | {item['verified_successes']}/{item['requests']} | "
            f"{item['success_rate_percent']:.2f}% | "
            f"{display(item['all_response_p50_seconds'], ' s')} | "
            f"{display(item['all_response_p95_seconds'], ' s')} | "
            f"{display(item['successful_plan_p95_seconds'], ' s')} |"
        )
    sequential_requests = sum(
        item["requests"] for item in report["sequential_summary"].values()
    )
    within_target = sum(
        item["verified_within_15_seconds"]
        for item in report["sequential_summary"].values()
    )
    recalculations = report["result_repeat_observations"]["successful_recalculations"]
    calculation_label = "recalculation" if recalculations == 1 else "recalculations"
    lines += [
        "",
        f"{within_target} of {sequential_requests} requests returned a verified "
        "plan within 15 seconds.",
        "Routing cold clears local routing and result caches. "
        "Prepared assets stay loaded.",
        "Routing warm retains routing data and clears the completed-result cache.",
        "Repeated requests retain the completed-result cache. "
        "Provider cache state is uncontrolled.",
        f"The repeated-request timings include {recalculations} "
        f"successful {calculation_label}.",
        "The timer includes HTTP, server queue time, planning, and persistence.",
        "Provider pacing occurs outside the timer. "
        "Percentiles include failed responses.",
        "",
        f"Concurrent batches used {sample['concurrent_routes']} distinct routes "
        "at each load level.",
        "",
        "| Concurrent requests | Cache state | Verified plans / requests "
        "| p95 | Batch requests/s |",
        "| --- | --- | --- | --- | --- |",
    ]
    for users in report["protocol"].get("concurrency_levels", [1, 3, 6]):
        for state in STATES:
            item = report["concurrent_summary"][f"{users}/{state}"]
            lines.append(
                f"| {users} | {LABELS[state]} | "
                f"{item['verified_successes']}/{item['requests']} | "
                f"{display(item['all_response_p95_seconds'], ' s')} | "
                f"{display(item['requests_per_second_in_measured_batches'])} |"
            )
    lines += [
        "",
        "Batch throughput includes client setup and saved-result checks.",
        f"The host used {report['environment']['logical_processors']} "
        "logical processors, "
        f"{report['environment']['waitress_threads']} Waitress threads, and "
        f"{report['environment']['planning_capacity']} planning slots.",
        "Other host work remained active.",
        "",
        f"Independent SciPy/HiGHS fuel plans were verified for "
        f"{quality['independent_verified_references']} routes.",
        "The solver used measured hosted matrices with independent price/access "
        "discovery batches. Missing cross-batch edges and nationwide omissions "
        "remain unknown. Reference itineraries "
        "used verified road directions and the same CSV prices, "
        "initial fuel, and reserve.",
        f"The median cost gap to these plans was "
        f"{display(quality['median_gap_to_mip_reference_percent'], '%')}. "
        "A negative gap means the API plan cost less.",
        f"The p95 cost gap was "
        f"{display(quality['p95_gap_to_mip_reference_percent'], '%')}.",
        f"Against a refill policy on the reference itineraries, median savings were "
        f"{display(quality['median_savings_against_refill_policy_percent'], '%')} "
        f"across {quality['refill_savings_comparable_cases']} comparisons.",
        "The policy buys fuel up to tank capacity or the remaining trip need plus "
        "reserve, whichever is smaller.",
        f"Combined savings across those trips were "
        f"{display(quality['aggregate_savings_against_refill_policy_percent'], '%')}.",
        f"The sample also contained {quality['zero_purchase_cases']} trips "
        "that needed no fuel purchase.",
    ]
    missing = quality["reference_failures"]
    if report.get("reference_reuse"):
        lines.append(
            "These reference itineraries were road-verified in the preceding "
            "capture. Cost gaps were recomputed against this installation's "
            "API responses; no new reference solves or verification calls "
            "were performed. The measurement file retains the source hash "
            "and original runtime identity."
        )
    if missing:
        count = sum(missing.values())
        route_label = "route" if count == 1 else "routes"
        lines.append(
            f"The measurement file records {count} "
            f"{route_label} without an independent cost comparison."
        )
    lines += [
        "",
        "Read the [full measurement file]"
        "(var/private-development/records/docs/evidence/performance.json) for "
        "route inputs, individual outcomes, successful-plan percentiles, "
        "and release hashes.",
        "Use the [measurement commands]"
        "(docs/LOCAL_SETUP.md#measure-performance-and-cost) "
        "to repeat the run.",
        END,
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "var/private-development/records/docs/evidence/performance.json",
    )
    parser.add_argument("--update-readme", action="store_true")
    parser.add_argument("--allocation", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if args.allocation is not None:
        allocation = json.loads(args.allocation.read_text(encoding="utf-8"))
        if (
            allocation["release"] != report["release"]
            or set(allocation["cases"]) != set(report["cases"])
            or allocation["repeats"] != report["protocol"]["repeats"]
            or not allocation["selection_frozen_before_final_observations"]
        ):
            raise ValueError("allocation differs from the measured scope")
        report["protocol"]["concurrency_levels"] = allocation["concurrency_levels"]
        report["environment"]["measurement_timezone"] = allocation.get(
            "measurement_timezone", "UTC"
        )
        report["allocation_sha256"] = hashlib.sha256(
            args.allocation.read_bytes()
        ).hexdigest()
    descriptor = json.loads((ROOT / "local-release.json").read_text(encoding="utf-8"))
    identities = {
        "release": descriptor["version"],
        "planner_sha256": planner_hash(ROOT),
        "asset_sha256": descriptor["sha256"],
        "candidate_sha256": descriptor["dataset"]["candidate_sha256"],
        "source_sha256": descriptor["dataset"]["source_sha256"],
    }
    if any(report.get(key) != value for key, value in identities.items()):
        raise ValueError("benchmark identity differs from the current release")
    if report["road"] != descriptor["road"]:
        raise ValueError("benchmark road checkpoint differs from the release")
    for name, expected_hash in report["benchmark_source_sha256"].items():
        actual = hashlib.sha256(
            (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        if actual != expected_hash:
            raise ValueError(f"benchmark source differs: {name}")
    report.update(summarize_report(report))
    check_public_fields(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.update_readme:
        path = ROOT / "README.md"
        text = path.read_text(encoding="utf-8")
        section = markdown(report)
        if START in text:
            begin = text.index(START)
            end = text.index(END, begin) + len(END)
            text = text[:begin] + section + text[end:]
        else:
            text = text.replace("## Start the API", section + "\n\n## Start the API", 1)
        path.write_text(text, encoding="utf-8")
    print(json.dumps(report["nationwide_sample"], indent=2))


if __name__ == "__main__":
    main()
