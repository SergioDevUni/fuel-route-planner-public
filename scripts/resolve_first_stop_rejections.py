"""Independently exclude every possible first offer on the fastest road profile."""

import argparse
import json
import os
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from scripts.atomic_checkpoint import write_checkpoint
from scripts.benchmark_budget import BenchmarkBudget
from scripts.feasibility_evidence import GEOD
from scripts.local_common import ROOT, load_local_env, sha256
from scripts.road_replay import encode


def possible_first_offers(start, stations, initial, reserve):
    # Same conservative origin/station snap and rounding allowances as preflight.
    usable = (initial - reserve) * Decimal(10) * Decimal("1609.344")
    return tuple(
        station
        for station in stations
        if GEOD.inv(*start.as_provider_pair(), *station.coordinates.as_provider_pair())[
            2
        ]
        - 1350
        <= usable
    )


def exclude_first_stops(matrix, stations, finish_index, baseline_seconds):
    """Shortest-time via-first-stop bounds; distance of fastest roads is not a bound.

    Every continuation after its first purchase takes at least the fastest
    station-to-finish duration on this profile. Adding purchases cannot shorten
    that bound. This establishes policy infeasibility on the measured profile,
    rather than claiming impossibility on every road engine or snapping policy.
    """
    if matrix.durations_seconds is None:
        raise ValueError("duration matrix required")
    ceiling = baseline_seconds + Decimal(1800)
    rows = []
    for index, station in enumerate(stations, 1):
        out = matrix.durations_seconds[0][index]
        onward = matrix.durations_seconds[index][finish_index]
        valid_snaps = (
            matrix.source_snap_meters[0] <= 1000
            and matrix.destination_snap_meters[index] <= 250
            and matrix.source_snap_meters[index] <= 250
            and matrix.destination_snap_meters[finish_index] <= 1000
        )
        via = out + onward if out is not None and onward is not None else None
        excluded = valid_snaps and (via is None or via > ceiling)
        rows.append(
            {
                "station_id": station.station_id,
                "valid_snaps": valid_snaps,
                "fastest_via_seconds": str(via) if via is not None else None,
                "minimum_extra_seconds": str(via - baseline_seconds)
                if via is not None
                else None,
                "excluded": excluded,
            }
        )
    return {
        "status": "checked_infeasible"
        if all(r["excluded"] for r in rows)
        else "undetermined",
        "method": (
            "exhaustive geodesic first-offer screen "
            "plus fastest-profile via-time bounds"
        ),
        "scope": "same ORS fastest driving-car profile and measured snap locations",
        "baseline_seconds": str(baseline_seconds),
        "maximum_extra_seconds": "1800",
        "first_offers": rows,
    }


def main():
    from app.domain.routing import Coordinates
    from app.planning.budget import RequestBudget
    from app.planning.candidates import CandidateIndex
    from app.routing.client import OpenRouteServiceAdapter

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--call-cap", type=int, default=1200)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("evidence output already exists")
    load_local_env()
    descriptor = json.loads((ROOT / "local-release.json").read_text())
    sample = json.loads(
        (
            ROOT
            / "var/private-development/records/docs/evidence/nationwide-checkpoint.json"
        ).read_text()
    )
    folder = args.assets_root / "var/station_candidates"
    if sha256(folder / "candidates.json") != descriptor["dataset"]["candidate_sha256"]:
        raise ValueError("candidate identity differs")
    index = CandidateIndex(folder, descriptor["dataset"]["source_sha256"])
    first = sample["feasibility"]["ca_regional_50"]["waypoints"][0]["requested"]
    start = Coordinates(**first)
    names = ("ca_regional_20", "ca_long_20")
    finishes = tuple(
        Coordinates(
            **sample["feasibility"][name.replace("20", "50")]["waypoints"][-1][
                "requested"
            ]
        )
        for name in names
    )
    stations = possible_first_offers(start, index.stations, Decimal(20), Decimal(2))
    points = (start, *(s.coordinates for s in stations), *finishes)
    adapter = OpenRouteServiceAdapter(os.environ["ORS_API_KEY"])
    report = {
        "candidate_sha256": descriptor["dataset"]["candidate_sha256"],
        "source_sha256": descriptor["dataset"]["source_sha256"],
        "source_release": sample["release"],
        "offers_checked": len(index.stations),
        "geodesically_excluded_offers": len(index.stations) - len(stations),
        "usable_range_miles": "180",
        "geodesic_snap_and_rounding_allowance_meters": 1350,
        "points": [asdict(p) for p in points],
        "rows": [],
    }
    with BenchmarkBudget(
        ROOT / "var/verification/benchmark-calls.json", limit=args.call_cap
    ) as pacing:
        before = pacing.data["attempts"]
        try:
            pacing.before_request()
            matrix = adapter.matrix(points, RequestBudget(deadline_seconds=45))
            report["matrix"] = json.loads(json.dumps(asdict(matrix), default=encode))
            for name, finish_index, finish in zip(
                names, range(len(stations) + 1, len(points)), finishes, strict=True
            ):
                pacing.before_request()
                baseline = adapter.route(
                    (start, finish), RequestBudget(deadline_seconds=45)
                )
                duration = matrix.durations_seconds[0][finish_index]
                if duration is None:
                    raise ValueError("matrix baseline unreachable")
                result = exclude_first_stops(
                    matrix,
                    stations,
                    finish_index,
                    max(duration, baseline.duration_seconds),
                )
                _, _, chord = GEOD.inv(
                    *start.as_provider_pair(), *finish.as_provider_pair()
                )
                if chord - 2100 <= 180 * 1609.344:
                    result["status"] = "undetermined"
                report["rows"].append(
                    {
                        "case": name,
                        "input": sample["sample"]["cases"][name],
                        "baseline_engine": baseline.engine_version,
                        "baseline_fetched_at": baseline.fetched_at.isoformat(),
                        "baseline_distance_meters": str(baseline.distance_meters),
                        **result,
                    }
                )
                report["hosted_attempts"] = pacing.data["attempts"] - before
                write_checkpoint(args.output, report)
        finally:
            adapter.close()
    print(
        json.dumps(
            {
                "hosted_attempts": report["hosted_attempts"],
                "rows": [
                    {"case": r["case"], "status": r["status"]} for r in report["rows"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
