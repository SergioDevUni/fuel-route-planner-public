"""Measure local OSRM directed distances on linked CSV site points."""

import argparse
import json
from decimal import Decimal
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.request import ProxyHandler, build_opener

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.routing.client import OpenRouteServiceAdapter
from scripts.check_pilot_routes import routing_key

LOCAL_HTTP = build_opener(ProxyHandler({}))


def request_json(url: str) -> tuple[dict, float]:
    started = perf_counter()
    with LOCAL_HTTP.open(url, timeout=10) as response:
        result = json.load(response)
    elapsed = (perf_counter() - started) * 1_000
    if result.get("code") != "Ok":
        raise ValueError(f"local OSRM request failed: {result.get('code')}")
    return result, elapsed


def measure(
    candidates_path: Path,
    base_url: str,
    repeats: int,
    ids: tuple[str, ...] = (),
    *,
    compare_ors: bool = False,
) -> dict:
    candidates = json.loads(candidates_path.read_text())
    if ids:
        by_id = {item["station_id"]: item for item in candidates}
        sites = [by_id[item] for item in ids]
    else:
        sites = [item for item in candidates if item["state"] == "DE"]
    if len(sites) < 2:
        raise ValueError("road benchmark requires at least two linked sites")
    coordinates = ";".join(f"{item['longitude']},{item['latitude']}" for item in sites)
    endpoint = f"{base_url.rstrip('/')}/table/v1/driving/{coordinates}"
    url = f"{endpoint}?annotations=distance,duration"
    first, cold_ms = request_json(url)
    samples = [request_json(url)[1] for _ in range(repeats)]
    source_ids = [item["station_id"] for item in sites]
    matrix = first["distances"]
    asymmetry = [
        [source_ids[i], source_ids[j], matrix[i][j], matrix[j][i]]
        for i in range(len(sites))
        for j in range(i + 1, len(sites))
        if matrix[i][j] is not None
        and matrix[j][i] is not None
        and abs(matrix[i][j] - matrix[j][i]) > 1
    ]
    report = {
        "site_ids": source_ids,
        "site_count": len(sites),
        "matrix_directed_meters": matrix,
        "snap_meters": [waypoint["distance"] for waypoint in first["sources"]],
        "asymmetric_pairs": asymmetry,
        "cold_ms": round(cold_ms, 3),
        "warm_ms": [round(sample, 3) for sample in samples],
        "warm_median_ms": round(sorted(samples)[len(samples) // 2], 3)
        if samples
        else None,
    }
    if compare_ors:
        adapter = OpenRouteServiceAdapter(routing_key())
        points = tuple(
            Coordinates(float(item["latitude"]), float(item["longitude"]))
            for item in sites
        )
        started = perf_counter()
        try:
            hosted = adapter.matrix(points, RequestBudget())
        finally:
            adapter.close()
        distance_errors = []
        duration_errors = []
        for source in range(len(points)):
            for target in range(len(points)):
                if source == target:
                    continue
                hosted_distance = hosted.distances_meters[source][target]
                hosted_duration = (
                    hosted.durations_seconds[source][target]
                    if hosted.durations_seconds is not None
                    else None
                )
                local_distance = first["distances"][source][target]
                local_duration = first["durations"][source][target]
                if (
                    hosted_distance is None
                    or hosted_duration is None
                    or local_distance is None
                    or local_duration is None
                ):
                    continue
                if hosted_distance > 0:
                    distance_errors.append(
                        abs(Decimal(str(local_distance)) - hosted_distance)
                        / hosted_distance
                        * 100
                    )
                if hosted_duration > 0:
                    duration_errors.append(
                        abs(Decimal(str(local_duration)) - hosted_duration)
                        / hosted_duration
                        * 100
                    )
        report["ors_comparison"] = {
            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
            "directed_pairs": len(distance_errors),
            "median_distance_error_pct": round(float(median(distance_errors)), 2),
            "max_distance_error_pct": round(float(max(distance_errors)), 2),
            "median_duration_error_pct": round(float(median(duration_errors)), 2),
            "max_duration_error_pct": round(float(max(duration_errors)), 2),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--ids", nargs="*", default=[])
    parser.add_argument("--compare-ors", action="store_true")
    arguments = parser.parse_args()
    if not 0 <= arguments.repeats <= 1_000:
        parser.error("repeats must be between 0 and 1000")
    print(
        json.dumps(
            measure(
                arguments.candidates,
                arguments.base_url,
                arguments.repeats,
                tuple(arguments.ids),
                compare_ors=arguments.compare_ors,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
