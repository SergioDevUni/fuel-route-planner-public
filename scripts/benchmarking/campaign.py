"""Frozen inputs and identities for an optional owner-selected development study.

Sample sizes, repetitions and quality gates describe this study, not assessment
requirements or conditions required to deliver the application.
"""

import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.fingerprints import benchmark_sources

CACHE_STATES = ("routing_cold", "routing_warm", "result_cached")
HOSTED_CONCURRENCY = (1, 3, 6)
OFFLINE_CONCURRENCY = (1, 3, 6, 12, 24)
REPEATS = 2
CONCURRENT_CASES = (
    "los_angeles_dallas",
    "seattle_denver",
    "chicago_miami",
    "new_york_los_angeles",
    "santa_fe_oklahoma_city",
    "las_vegas_san_diego",
    "san_antonio_new_orleans",
    "detroit_cincinnati",
    "boise_spokane",
    "charlotte_richmond",
    "fargo_sioux_falls",
    "birmingham_jackson",
    "boston_buffalo",
    "pittsburgh_nashville",
    "salt_lake_boise",
    "el_paso_austin",
    "minneapolis_rapid_city",
    "reno_sacramento_reserve",
)


def performance_inputs() -> dict:
    return {
        name: {
            "start": case.start,
            "finish": case.finish,
            "initial_fuel_gallons": case.initial,
            "reserve_gallons": case.reserve,
        }
        for name, case in BENCHMARK_CASES.items()
    }


def campaign_spec(root: Path, planner: str) -> dict:
    raw = (root / "tests/fixtures/nationwide-sample.json").read_bytes()
    sample = json.loads(raw)
    descriptor = json.loads((root / "local-release.json").read_text())
    return {
        "planner_sha256": planner,
        "benchmark_source_sha256": benchmark_sources(root),
        "asset_sha256": descriptor["sha256"],
        "candidate_sha256": descriptor["dataset"]["candidate_sha256"],
        "source_sha256": descriptor["dataset"]["source_sha256"],
        "road": descriptor["road"],
        "sample_sha256": hashlib.sha256(raw).hexdigest(),
        "nationwide_sample": sample,
        "performance_cases": performance_inputs(),
        "concurrent_cases": list(CONCURRENT_CASES),
        "protocol": {
            "repeats": REPEATS,
            "cache_states": list(CACHE_STATES),
            "hosted_concurrency": list(HOSTED_CONCURRENCY),
            "offline_concurrency": list(OFFLINE_CONCURRENCY),
        },
    }


def check_identity(report: dict, spec: dict, *, nationwide: bool = False) -> None:
    keys = [
        "planner_sha256",
        "benchmark_source_sha256",
        "asset_sha256",
        "candidate_sha256",
        "source_sha256",
        "road",
    ]
    if nationwide:
        keys.append("sample_sha256")
    for key in keys:
        if not spec.get(key) or report.get(key) != spec[key]:
            raise ValueError(f"evidence identity differs: {key}")


def check_performance_protocol(report: dict, spec: dict) -> None:
    expected = spec["performance_cases"]
    if set(report["cases"]) != set(expected):
        raise ValueError("performance case identities differ from frozen campaign")
    for name, case in report["cases"].items():
        if {key: case.get(key) for key in expected[name]} != expected[name]:
            raise ValueError(f"performance input differs: {name}")
    protocol = report["protocol"]
    if protocol["repeats"] != REPEATS:
        raise ValueError("development profile requires two trials")
    if protocol.get("concurrency_levels") != list(HOSTED_CONCURRENCY):
        raise ValueError("development profile requires concurrency levels 1/3/6")
    if {row["case"] for row in report["concurrent_rows"]} != set(CONCURRENT_CASES):
        raise ValueError("development profile requires all 18 concurrent routes")


def prepared_performance_cases() -> dict:
    return {name: asdict(case) for name, case in BENCHMARK_CASES.items()}


def check_reference_coverage(report: dict) -> None:
    """Tie reference coverage and reported gaps to the actual cold observations."""
    cold = {
        row["case"]: row
        for row in report["rows"]
        if row["cache_state"] == "routing_cold" and row["trial"] == 1
    }
    for reference in report["references"]:
        observation = cold[reference["case"]]
        valid = observation.get("contract_ok") is True
        if reference.get("online_valid") is not valid:
            raise ValueError("reference validity differs from measured observation")
        if not valid:
            if reference.get("gap_percent") is not None:
                raise ValueError("failed observation cannot contribute a cost gap")
            continue
        cost = Decimal(observation["purchase_cost"])
        if (
            not cost.is_finite()
            or cost < 0
            or Decimal(reference["online_cost"]) != cost
        ):
            raise ValueError("reference online cost differs from measured observation")
        baseline = Decimal(reference["reference_cost"])
        if not baseline.is_finite() or baseline < 0:
            raise ValueError("reference cost must be finite and nonnegative")
        if cost == 0:
            if reference["status"] != "zero_purchase_case" or baseline != 0:
                raise ValueError("zero-purchase case needs a zero reference outcome")
            continue
        if reference["status"] != "verified_independent_mip" or not any(
            batch.get("status") == "measured"
            for batch in reference.get("discovery", {}).get("batches", [])
        ):
            raise ValueError(
                "positive purchase case lacks measured independent coverage"
            )
        if not any(
            candidate.get("source") == "independent_same_profile_mip"
            and candidate.get("valid") is True
            and Decimal(candidate["cost"]) == baseline
            for candidate in reference.get("candidates", [])
        ):
            raise ValueError(
                "reference lacks a matching verified independent itinerary"
            )
        expected_gap = (cost - baseline) / baseline * 100 if baseline else None
        gap = reference.get("gap_percent")
        if (
            expected_gap is None
            or gap is None
            or abs(Decimal(gap) - expected_gap) > Decimal("0.000001")
        ):
            raise ValueError("reference gap differs from measured costs")
