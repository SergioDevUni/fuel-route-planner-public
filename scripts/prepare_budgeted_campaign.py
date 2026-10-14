"""Prepare a shared-cap campaign without making external requests."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.benchmarking.budgeted_campaign import (
    CALL_CAP,
    select_sample,
    specification,
)
from scripts.benchmarking.fingerprints import benchmark_sources
from scripts.local_common import ROOT, planner_hash


def encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def prepare(directory: Path, source: Path, *, call_ledger: Path | None = None) -> dict:
    if directory.exists():
        raise ValueError("output exists; choose a fresh campaign directory")
    raw = source.read_bytes()
    sample = select_sample(json.loads(raw))
    sample_bytes = encoded(sample)
    descriptor = json.loads((ROOT / "local-release.json").read_text())
    if sample["candidate_sha256"] != descriptor["dataset"]["candidate_sha256"]:
        raise ValueError("sample candidate differs from the installed release")
    planner = planner_hash(ROOT)
    if planner != descriptor["planner_sha256"]:
        raise ValueError("planner differs from the release descriptor")
    ledger = call_ledger or directory / "calls.json"
    if ledger.exists() and json.loads(ledger.read_text())["limit"] != CALL_CAP:
        raise ValueError("existing ledger differs from the 960-attempt cap")
    common = ["--call-ledger", str(ledger), "--call-cap", str(CALL_CAP)]
    report = {
        "schema": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "awaiting_campaign",
        **specification(sample),
        "planner_sha256": planner,
        "benchmark_source_sha256": benchmark_sources(ROOT),
        "source_sample_sha256": hashlib.sha256(raw).hexdigest(),
        "sample_sha256": hashlib.sha256(sample_bytes).hexdigest(),
        "asset_sha256": descriptor["sha256"],
        "candidate_sha256": descriptor["dataset"]["candidate_sha256"],
        "source_sha256": descriptor["dataset"]["source_sha256"],
        "road": descriptor["road"],
        "call_ledger": str(ledger),
        "invocations": [
            [
                "-m",
                "scripts.benchmark_nationwide_delivery",
                "--sample",
                str(directory / "nationwide-sample.json"),
                "--output",
                str(directory / "nationwide.json"),
                *common,
            ],
            [
                "-m",
                "scripts.benchmark_release_performance",
                "--output",
                str(directory / "performance.json"),
                "--repeats",
                "1",
                "--concurrency",
                "1",
                "3",
                "6",
                "--cases",
                *specification(sample)["performance_cases"],
                *common,
            ],
        ],
        "execution_notes": "Run sequentially from a clean installed candidate "
        "with matching identities and private provider configuration. Use "
        "--resume with the same output and ledger after interruption.",
    }
    directory.mkdir(parents=True)
    (directory / "nationwide-sample.json").write_bytes(sample_bytes)
    if not ledger.exists():
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_bytes(
            encoded({"schema": 1, "limit": CALL_CAP, "attempts": 0, "calls": []})
        )
    (directory / "campaign.json").write_bytes(encoded(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--call-ledger", type=Path, help="Reuse a shared ledger without resetting it"
    )
    parser.add_argument(
        "--sample", type=Path, default=ROOT / "tests/fixtures/nationwide-sample.json"
    )
    args = parser.parse_args()
    try:
        report = prepare(
            args.output_dir.resolve(),
            args.sample,
            call_ledger=args.call_ledger.resolve() if args.call_ledger else None,
        )
    except ValueError as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "scope",
                    "hard_call_cap",
                    "conservative_total_allowance",
                    "reserve_attempts",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
