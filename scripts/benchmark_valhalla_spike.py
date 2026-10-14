"""Measure Valhalla's local matrix on the same linked site set as OSRM."""

import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.request import ProxyHandler, Request, build_opener

LOCAL_HTTP = build_opener(ProxyHandler({}))


def post_json(url: str, payload: dict) -> tuple[dict, float]:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = perf_counter()
    with LOCAL_HTTP.open(request, timeout=30) as response:
        result = json.load(response)
    return result, (perf_counter() - started) * 1_000


def measure(
    candidates_path: Path, base_url: str, ids: tuple[str, ...], repeats: int
) -> dict:
    candidates = json.loads(candidates_path.read_text())
    by_id = {item["station_id"]: item for item in candidates}
    sites = [by_id[item] for item in ids]
    points = [{"lat": item["latitude"], "lon": item["longitude"]} for item in sites]
    request = {
        "sources": points,
        "targets": points,
        "costing": "auto",
        "units": "kilometers",
        "verbose": False,
    }
    url = f"{base_url.rstrip('/')}/sources_to_targets"
    first, cold_ms = post_json(url, request)
    distances = first["sources_to_targets"]["distances"]
    if len(distances) != len(sites) or any(len(row) != len(sites) for row in distances):
        raise ValueError("Valhalla matrix has an unexpected shape")
    samples = [post_json(url, request)[1] for _ in range(repeats)]
    return {
        "site_ids": list(ids),
        "distance_semantics": "distance_of_auto_profile_route",
        "algorithm": first.get("algorithm"),
        "matrix_directed_km": distances,
        "cold_ms": round(cold_ms, 3),
        "warm_ms": [round(sample, 3) for sample in samples],
        "warm_median_ms": round(median(samples), 3) if samples else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--repeats", type=int, default=30)
    arguments = parser.parse_args()
    if not 0 <= arguments.repeats <= 1_000:
        parser.error("repeats must be between 0 and 1000")
    print(
        json.dumps(
            measure(
                arguments.candidates,
                arguments.base_url,
                tuple(arguments.ids),
                arguments.repeats,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
