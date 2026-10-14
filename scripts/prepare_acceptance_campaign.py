"""Prepare the optional historical development campaign without provider calls.

Its fixed samples and gates were chosen by the owner for internal development;
they do not define assessment requirements or release obligations.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.benchmarking.campaign import (
    CACHE_STATES,
    CONCURRENT_CASES,
    HOSTED_CONCURRENCY,
    OFFLINE_CONCURRENCY,
    REPEATS,
    campaign_spec,
    prepared_performance_cases,
)
from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.fingerprints import benchmark_sources
from scripts.local_common import ROOT, planner_hash


def prepare(sample: dict, sample_hash: str, planner: str, harness: dict) -> dict:
    if len(sample["cases"]) != 290 or len(BENCHMARK_CASES) != 54:
        raise ValueError("the fixed 290-input and 54-route samples are required")
    # Every estimate includes worst-case retries within the four-attempt budget.
    allowances = {
        "nationwide_preflight": 290 * 4,
        "nationwide_measurements": 290 * 4,
        "performance_sequential": len(BENCHMARK_CASES)
        * REPEATS
        * len(CACHE_STATES)
        * 4,
        "performance_concurrent": len(CONCURRENT_CASES)
        * len(HOSTED_CONCURRENCY)
        * len(CACHE_STATES)
        * 4,
        "independent_references": 54 * (2 * 4 + 4),
    }
    return {
        "schema": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "awaiting_campaign",
        "purpose": "Optional owner-selected development diagnostic; "
        "not an assessment requirement or release obligation.",
        "hosted_execution_authorized": False,
        "planner_sha256": planner,
        "benchmark_source_sha256": harness,
        "sample_sha256": sample_hash,
        "nationwide_sample": sample,
        "performance_cases": prepared_performance_cases(),
        "release_specification": campaign_spec(ROOT, planner),
        "protocol": {
            "repeats": REPEATS,
            "cache_states": list(CACHE_STATES),
            "hosted_concurrency": list(HOSTED_CONCURRENCY),
            "offline_concurrency": list(OFFLINE_CONCURRENCY),
        },
        "hosted_attempt_allowances": allowances,
        "conservative_total_allowance": sum(allowances.values()),
        "estimate_limits": "Caches may reduce calls; reference retries are budgeted. "
        "Extra campaigns or new reference policies require a revised estimate.",
        "existing_campaign": {"used": 1282, "cap": 1300, "unused": 18},
        "gates": {
            "feasible_completion_percent_min": 99,
            "median_reference_gap_percent_max": 1,
            "p95_reference_gap_percent_max": 5,
            "response_deadline_seconds": 15,
            "reference_coverage": "every positive-cost feasible case",
            "restore_install_platforms": ["Windows", "Linux"],
        },
        "prerequisites": [
            "Freeze a new candidate release identity and package its matching assets.",
            "Authorize a new hosted-call ledger and budget; preserve old reports.",
            "Run independent preflight before measurements; retain unknown outcomes.",
            "Run acceptance against this exact planner and harness identity.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample", type=Path, default=ROOT / "tests/fixtures/nationwide-sample.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new campaign path")
    raw = args.sample.read_bytes()
    report = prepare(
        json.loads(raw),
        hashlib.sha256(raw).hexdigest(),
        planner_hash(ROOT),
        benchmark_sources(ROOT),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": report["status"],
                "conservative_attempt_allowance": report[
                    "conservative_total_allowance"
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
