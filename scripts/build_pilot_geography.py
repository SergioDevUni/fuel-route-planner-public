"""Build local city search seeds for the 100-station coordinate pilot."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from app.domain.text import canonical_text

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "var/pilot/stations.json"
PLACES = ROOT / "data/prepared/locations/places.ndjson"
OUTPUT = ROOT / "var/pilot/geographic_seeds.json"


def build_seeds(sample: list[dict], places: list[dict]) -> list[dict]:
    names: dict[tuple[str, str], list[dict]] = defaultdict(list)
    aliases: dict[tuple[str, str], list[dict]] = defaultdict(list)
    wanted = {(s["state"], canonical_text(s["city"])) for s in sample}
    for place in places:
        name_key = (place["state"], canonical_text(place["name"]))
        if name_key in wanted:
            names[name_key].append(place)
        for alias in place["aliases"]:
            alias_key = (place["state"], canonical_text(alias))
            if alias_key in wanted and alias_key != name_key:
                aliases[alias_key].append(place)
    result = []
    for station in sample:
        key = (station["state"], canonical_text(station["city"]))
        exact = names[key]
        matches = exact if exact else aliases[key]
        unique = {place["id"]: place for place in matches}
        centers = [
            {
                "place_id": place["id"],
                "name": place["name"],
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "population": place["population"],
            }
            for place in sorted(unique.values(), key=lambda item: item["id"])
        ]
        if not centers:
            status = "missing"
        elif len(centers) > 1:
            status = "ambiguous"
        elif exact:
            status = "exact_name"
        else:
            status = "alias"
        result.append(
            {
                "opis_id": station["opis_id"],
                "city": station["city"],
                "state": station["state"],
                "status": status,
                "centers": centers,
            }
        )
    return result


def main() -> None:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    places = [
        json.loads(line) for line in PLACES.read_text(encoding="utf-8").splitlines()
    ]
    seeds = build_seeds(sample["stations"], places)
    counts = Counter(item["status"] for item in seeds)
    document = {
        "source_sha256": sample["source_sha256"],
        "purpose": "regional Overture query seeds, not station coordinates",
        "counts": dict(sorted(counts.items())),
        "stations": seeds,
    }
    OUTPUT.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(seeds)} geographic search seeds: {dict(counts)}")


if __name__ == "__main__":
    main()
