"""Freeze six route inputs per contiguous state before live measurements."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from scripts.feasibility_evidence import GEOD
from scripts.local_common import ROOT

CONTIGUOUS = set(
    "AL AZ AR CA CO CT DE FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "tests/fixtures/nationwide-sample.json"
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError(
            "sample already exists; use a new output path to freeze a new sample"
        )
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from app.locations.models import GazetteerVersion, Place

    version = GazetteerVersion.objects.get(status="active")
    all_places = list(
        Place.objects.filter(version=version, state__in=CONTIGUOUS)
        .order_by("state", "-population", "feature_id")
        .values("state", "name", "feature_id", "latitude", "longitude", "population")
    )
    by_state = {
        state: [place for place in all_places if place["state"] == state]
        for state in sorted(CONTIGUOUS)
    }
    anchors = [places[0] for places in by_state.values()]
    regional_places = [place for places in by_state.values() for place in places[:5]]
    cases = {}

    def distance(a, b):
        return (
            GEOD.inv(a["longitude"], a["latitude"], b["longitude"], b["latitude"])[2]
            / 1609.344
        )

    def add(name, start, finish, initial, reserve, stratum):
        cases[name] = {
            "start": {"type": "place_id", "id": start["feature_id"]},
            "finish": {"type": "place_id", "id": finish["feature_id"]},
            "initial_fuel_gallons": str(initial),
            "reserve_gallons": str(reserve),
            "stratum": stratum,
            "start_name": start["name"],
            "finish_name": finish["name"],
            "start_state": start["state"],
            "finish_state": finish["state"],
            "geodesic_miles": round(distance(start, finish), 3),
        }

    for state, places in by_state.items():
        origin = places[0]
        near = [place for place in places if 20 <= distance(origin, place) <= 90]
        if not near:
            raise ValueError(f"no short-distance place in {state}")
        short = max(near, key=lambda place: (place["population"], place["feature_id"]))
        region = [
            place for place in regional_places if 120 <= distance(origin, place) <= 350
        ]
        regional = min(
            region,
            key=lambda place: (abs(distance(origin, place) - 220), place["feature_id"]),
        )
        long = [place for place in anchors if 550 <= distance(origin, place) <= 900]
        distant = min(
            long,
            key=lambda place: (abs(distance(origin, place) - 700), place["feature_id"]),
        )
        for label, destination, fuel_levels in (
            ("short", short, (5, 50)),
            ("regional", regional, (20, 50)),
            ("long", distant, (20, 50)),
        ):
            for initial in fuel_levels:
                add(
                    f"{state.lower()}_{label}_{initial}",
                    origin,
                    destination,
                    initial,
                    1 if initial == 5 else 2,
                    label,
                )
    oakland = next(
        place
        for place in all_places
        if place["state"] == "CA" and place["name"] == "Oakland"
    )
    fresno = next(
        place
        for place in all_places
        if place["state"] == "CA" and place["name"] == "Fresno"
    )
    for fuel in (10, 50):
        add(f"oakland_fresno_{fuel}", oakland, fresno, fuel, 2, "regression")
    descriptor = json.loads((ROOT / "local-release.json").read_text(encoding="utf-8"))
    content = {
        "schema": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "selection": "Deterministic equal-state strata; populous origins; "
        "short, regional and long geodesic distances; two fuel levels. "
        "Inputs remain fixed after outcomes.",
        "states": sorted(CONTIGUOUS),
        "gazetteer_sha256": version.source_sha256,
        "candidate_sha256": descriptor["dataset"]["candidate_sha256"],
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "cases": len(cases),
                "states": len(CONTIGUOUS),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
