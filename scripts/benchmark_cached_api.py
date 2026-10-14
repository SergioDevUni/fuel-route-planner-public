"""Profile repeated cached road plans through HTTP without provider calls."""

import argparse
import hashlib
import json
import logging
import os
import threading
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.benchmarking.fingerprints import benchmark_sources
from scripts.local_common import ROOT, planner_hash, sha256


def measure(
    facts,
    *,
    users=24,
    seconds=20,
    trials=3,
    payload_input=None,
    payload_output=None,
    profile=True,
    external_clients=False,
):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.db import connections
    from waitress import wasyncore

    from app.api.cache import ResultCache
    from app.api.deadline import shutdown_planning
    from scripts.benchmarking.cached_payload import prepare_payload
    from scripts.benchmarking.cached_profile import CachedProfile, summarize_stages
    from scripts.benchmarking.capacity import CapacityProbe
    from scripts.benchmarking.capacity_environment import resource_environment
    from scripts.benchmarking.capacity_sustained import sustained_load
    from scripts.benchmarking.offline_replay import load_replays

    if external_clients:
        from scripts.benchmarking.external_clients import sustained_load

    index, cases = load_replays(ROOT, facts)
    initial_identity = planner_hash(ROOT)
    selected = {"los_angeles_dallas": cases["los_angeles_dallas"]}
    context = threading.local()

    class ObservedCache(ResultCache):
        def get_completed(self, key):
            budget = getattr(context, "budget", None)
            if budget is None:
                return super().get_completed(key)
            with budget.stage("cache_lookup_copy"):
                return super().get_completed(key)

    with TemporaryDirectory(prefix="fuel-cached-profile-") as folder:
        probe = CapacityProbe(
            Path(folder) / "runtime.sqlite3", replays=(index, selected)
        )
        probe.cache = ObservedCache()
        probe.mode["replay"] = True
        original_budget = probe.budget

        def budget():
            value = original_budget()
            context.budget = value
            return value

        probe.start()
        rows = []
        payload_identity, payload_bytes = None, None
        measurement_error = None
        previous_logging = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            with ExitStack() as stack:
                stack.enter_context(CachedProfile(context, enabled=profile))
                for name, value in {
                    "trip_cache": probe.cache,
                    "request_coalescer": probe.coalescer,
                    "calculation_keys": probe.key,
                    "calculate": probe.calculate,
                    "RequestBudget": budget,
                }.items():
                    stack.enter_context(patch("app.api.views." + name, value))
                payload_identity, payload_bytes, key, payload = prepare_payload(
                    probe, index, facts, initial_identity, payload_input, payload_output
                )
                for trial in range(trials):
                    # Independent arrival windows start with the same valid cache
                    # snapshot. Production expiry remains unchanged.
                    probe.cache.put_completed(key, payload)
                    before = len(probe.observed)
                    row = sustained_load(probe, users, seconds, same=True)
                    budgets = probe.observed[before:]
                    row["trial"] = trial + 1
                    summarize_stages(row, budgets)
                    row["cached_only"] = (
                        row["planner_calls"] == 0 and row["replay_attempts"] == 0
                    )
                    row["passed"] = row["passed"] and row["cached_only"]
                    rows.append(row)
                    if not row["passed"]:
                        measurement_error = "Cached trial failed its workload checks"
                        break
        except Exception as error:
            measurement_error = type(error).__name__ + ": " + str(error)
        finally:
            drained = shutdown_planning(timeout=10)
            probe.server.task_dispatcher.shutdown(timeout=5)
            wasyncore.close_all(map=probe.channels)
            probe.thread.join(timeout=2)
            connections.close_all()
            logging.disable(previous_logging)
    return {
        "schema": 1,
        "planner_sha256": initial_identity,
        "measurement_sources_unchanged": initial_identity == planner_hash(ROOT),
        "cached_payload_sha256": payload_identity,
        "cached_payload_bytes": payload_bytes,
        "preloaded_verified_payload": payload_input is not None,
        "persistence_profiling": profile,
        "road_facts_sha256": sha256(facts),
        "profiler_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "benchmark_source_sha256": benchmark_sources(ROOT),
        "measurement_error": measurement_error,
        "independent_cache_preload_per_trial": True,
        "load_generator": "separate_process" if external_clients else "shared_process",
        "resources": resource_environment(),
        "case": "los_angeles_dallas",
        "users": users,
        "arrival_window_seconds": seconds,
        "trials": rows,
        "shutdown_drained": drained,
        "hosted_attempts": 0,
        "passed": drained
        and measurement_error is None
        and initial_identity == planner_hash(ROOT)
        and len(rows) == trials
        and all(row["passed"] for row in rows),
        "two_second_p95_met": len(rows) == trials
        and all(row["p95_seconds"] <= 2 for row in rows),
        "scope": "Captured long-trip payload; cached HTTP POST, fresh SQLite saved "
        "results and independent fuel/price/access checks; no live provider timing",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--road-facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--users", type=int, default=24)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--cpu-affinity", type=int)
    parser.add_argument("--payload-input", type=Path)
    parser.add_argument("--payload-output", type=Path)
    parser.add_argument("--no-profile", action="store_true")
    parser.add_argument("--external-clients", action="store_true")
    args = parser.parse_args()
    if (
        args.output.exists()
        or (args.payload_output and args.payload_output.exists())
        or not 1 <= args.users <= 64
        or not 1 <= args.seconds <= 300
        or not 1 <= args.trials <= 10
    ):
        parser.error("Fresh output, 1-64 users, 1-300 seconds and 1-10 trials required")
    if args.cpu_affinity is not None:
        from scripts.benchmarking.capacity_environment import restrict_cpu_affinity

        restrict_cpu_affinity(args.cpu_affinity)
    report = measure(
        args.road_facts,
        users=args.users,
        seconds=args.seconds,
        trials=args.trials,
        payload_input=args.payload_input,
        payload_output=args.payload_output,
        profile=not args.no_profile,
        external_clients=args.external_clients,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "p95_seconds": [r["p95_seconds"] for r in report["trials"]],
                "two_second_p95_met": report["two_second_p95_met"],
            }
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
