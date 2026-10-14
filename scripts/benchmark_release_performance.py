"""Measure a release through HTTP, captured roads, and independent references."""

import argparse
from pathlib import Path

from scripts.benchmarking.campaign import HOSTED_CONCURRENCY, REPEATS
from scripts.local_common import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument(
        "--concurrency", nargs="+", type=int, default=list(HOSTED_CONCURRENCY)
    )
    parser.add_argument("--skip-references", action="store_true")
    parser.add_argument("--skip-concurrency", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--call-ledger",
        type=Path,
        default=ROOT / "var/verification/benchmark-calls.json",
    )
    parser.add_argument("--call-cap", type=int, default=1000)
    args = parser.parse_args()
    if args.repeats < 1 or any(value not in (1, 3, 6) for value in args.concurrency):
        parser.error("positive repeats and concurrency 1, 3, or 6 are required")
    from scripts.benchmarking.performance_campaign import run_campaign

    run_campaign(args, parser)


if __name__ == "__main__":
    main()
