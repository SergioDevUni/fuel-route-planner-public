"""Measure offline HTTP capacity without routing provider calls."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.benchmarking.capacity import run_probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--road-facts", type=Path)
    parser.add_argument("--admission-wait", type=float, default=4.0)
    parser.add_argument("--threads", type=int, default=32)
    parser.add_argument("--sustained-seconds", type=float, default=20)
    parser.add_argument(
        "--cpu-affinity",
        type=int,
        help="Limit this benchmark process to N available CPUs",
    )
    args = parser.parse_args()
    if not 0 <= args.admission_wait <= 4 or not 8 <= args.threads <= 64:
        parser.error("admission wait 0-4 seconds and 8-64 threads required")
    if args.output.exists():
        parser.error("output exists; choose a fresh evidence path")
    if not 1 <= args.sustained_seconds <= 300:
        parser.error("sustained duration must be 1-300 seconds")
    if args.cpu_affinity is not None:
        from scripts.benchmarking.capacity_environment import restrict_cpu_affinity

        restrict_cpu_affinity(args.cpu_affinity)
    with TemporaryDirectory(prefix="fuel-capacity-") as folder:
        report = run_probe(
            Path(folder) / "runtime.sqlite3",
            facts_path=args.road_facts,
            admission_wait=args.admission_wait,
            threads=args.threads,
            sustained_seconds=args.sustained_seconds,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "scenarios": len(report["rows"]),
                "passed": report["passed"],
                "hosted_attempts": 0,
            }
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
