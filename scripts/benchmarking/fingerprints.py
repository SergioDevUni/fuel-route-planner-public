"""Freeze the complete harness implementation when starting/resuming campaigns."""

import hashlib
from pathlib import Path


def benchmark_sources(root: Path) -> dict[str, str]:
    shared = sorted((root / "scripts/benchmarking").glob("*.py"))
    names = (
        "benchmark_release_performance.py",
        "benchmark_nationwide_quality.py",
        "performance_metrics.py",
        "nationwide_quality_metrics.py",
        "reference_scope.py",
        "benchmark_budget.py",
        "atomic_checkpoint.py",
        "road_replay.py",
        "benchmark_nationwide_delivery.py",
        "nationwide_preflight.py",
        "feasibility_evidence.py",
        "witness_costs.py",
        "benchmark_offline_capacity.py",
        "benchmark_cached_api.py",
        "verify_orchestration_replay.py",
        "prepare_acceptance_campaign.py",
        "prepare_budgeted_campaign.py",
        "release_acceptance.py",
    )
    paths = [root / "scripts" / name for name in names] + shared
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for path in paths
    }
