"""Select a reproducible 100-ID review sample from the supplied fuel file.

This creates a review queue, not geocoded or accepted station coordinates.
"""

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from app.stations.catalogs.regions import STATES as STATES

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/fuel-prices-for-be-assessment.csv"
GAZETTEER = ROOT / "data/prepared/locations/places.ndjson"
AUDIT = ROOT / "data/audit/source-profile.json"
OUTPUT = ROOT / "var/pilot/stations.json"
CHAIN = re.compile(
    r"\b(PILOT|FLYING J|LOVE'?S|PETRO|SPEEDWAY|CIRCLE K|KWIK TRIP|ROAD RANGER|TA)\b",
    re.IGNORECASE,
)
NUMBERED = re.compile(r"^\s*\d+\s+\S+")


def sort_key(station: dict[str, object]) -> str:
    return hashlib.sha256(str(station["opis_id"]).encode()).hexdigest()


def population_index() -> dict[tuple[str, str], int]:
    by_city: dict[tuple[str, str], int] = {}
    with GAZETTEER.open(encoding="utf-8") as stream:
        for line in stream:
            place = json.loads(line)
            key = (place["state"], place["name"].casefold())
            by_city[key] = max(by_city.get(key, 0), place["population"])
    return by_city


def source_stations() -> list[dict[str, object]]:
    expected = json.loads(AUDIT.read_text(encoding="utf-8"))["sha256"]
    actual = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("source CSV hash does not match the audited input")
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["State"].strip().upper() in STATES:
                groups[row["OPIS Truckstop ID"]].append(row)
    if len(groups) != 6626:
        raise ValueError("expected 6,626 distinct US station identities")
    populations = population_index()
    stations = []
    for opis_id, rows in groups.items():
        first = rows[0]
        aliases = list(dict.fromkeys(row["Truckstop Name"] for row in rows))
        city_population = populations.get((first["State"], first["City"].casefold()))
        address = first["Address"]
        station = {
            "opis_id": opis_id,
            "name": first["Truckstop Name"],
            "aliases": aliases,
            "address": address,
            "city": first["City"],
            "state": first["State"],
            "city_population": city_population,
            "tags": [
                tag
                for tag, present in (
                    ("numbered_address", bool(NUMBERED.match(address))),
                    ("exit_address", "EXIT" in address.upper()),
                    ("alias", len(aliases) > 1),
                    ("california", first["State"] == "CA"),
                    ("chain", bool(CHAIN.search(first["Truckstop Name"]))),
                    ("rural", city_population is not None and city_population < 10000),
                    (
                        "urban",
                        city_population is not None and city_population >= 100000,
                    ),
                )
                if present
            ],
        }
        stations.append(station)
    return stations


def select(stations: list[dict[str, object]]) -> list[dict[str, object]]:
    by_id = {str(item["opis_id"]): item for item in stations}
    # The bounded Chicago/Joliet extract can be reviewed against these IDs.
    regional_ids = (
        "1653",  # Road Ranger, Chicago
        "63946",  # Minit Mart, Joliet
        "69566",  # Pilot, Joliet; source name alias
        "69596",  # Speedway, Des Plaines
        "69683",  # Good to Go, Chicago
        "70034",  # Speedway, Bedford Park
        "70666",  # Thorntons, Chicago
        "71318",  # Love's, Elk Grove Village
    )
    selected: dict[str, dict[str, object]] = {key: by_id[key] for key in regional_ids}

    def add(tag: str, count: int) -> None:
        candidates = sorted(
            (item for item in stations if tag in item["tags"]), key=sort_key
        )
        for item in candidates:
            if len([one for one in selected.values() if tag in one["tags"]]) >= count:
                break
            selected[str(item["opis_id"])] = item

    # Exhaust rare strata, then fill common patterns without duplicate IDs.
    add("numbered_address", 6)
    add("california", 8)
    add("alias", 18)
    add("rural", 30)
    add("urban", 15)
    add("exit_address", 50)
    add("chain", 25)
    if len(selected) > 100:
        raise ValueError("stratification exceeds the target sample size")

    # Prefer states not yet represented, while preserving stable ID hash order.
    candidates = sorted(stations, key=sort_key)
    while len(selected) < 100:
        counts = Counter(str(item["state"]) for item in selected.values())
        remaining = [
            item for item in candidates if str(item["opis_id"]) not in selected
        ]
        if not remaining:
            raise ValueError("not enough station identities for the pilot")
        choice = min(
            remaining,
            key=lambda item: (counts[str(item["state"])], sort_key(item)),
        )
        selected[str(choice["opis_id"])] = choice
    return sorted(
        selected.values(), key=lambda item: (str(item["state"]), sort_key(item))
    )


def main() -> None:
    sample = select(source_stations())
    tags = Counter(tag for item in sample for tag in item["tags"])
    states = Counter(str(item["state"]) for item in sample)
    document = {
        "purpose": "coordinate-candidate review; no location accepted",
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "method": (
            "stable SHA-256 ID ordering with rare-stratum quotas "
            "and state-balanced fill"
        ),
        "count": len(sample),
        "tag_counts": dict(sorted(tags.items())),
        "state_counts": dict(sorted(states.items())),
        "stations": sample,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(sample)} review IDs to {OUTPUT}")
    print(f"States: {len(states)}; tags: {dict(sorted(tags.items()))}")


if __name__ == "__main__":
    main()
