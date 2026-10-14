"""Rank state Overture site candidates for unmatched CSV IDs; accept none."""

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict

from app.stations.catalogs.names import name_similarity, tokens
from app.stations.catalogs.paths import ROOT
from app.stations.catalogs.source_queue import build_queue
from scripts.build_overture_state_catalog import OUTPUT_ROOT, RELEASE

REVIEW_ROOT = ROOT / "var/overture_states"


def score(station: dict, place: dict, state: str) -> tuple[float, dict] | None:
    addresses = [
        address
        for address in place["addresses"] or []
        if (address.get("region") or "").upper() == state
        and (address.get("country") or "").upper() == "US"
    ]
    if not addresses:
        return None
    name = place["name"] or ""
    similarity = name_similarity(station["names"], name)
    cities = {city.casefold().strip() for city in station["cities"]}
    locality = any(
        (address.get("locality") or "").casefold().strip() in cities
        for address in addresses
    )
    city_conflict = not locality and any(
        (address.get("locality") or "").strip() for address in addresses
    )
    source_numbers = {
        match
        for source_name in station["names"]
        for match in re.findall(r"#\s*(\d+)", source_name)
    }
    candidate_numbers = set(re.findall(r"(?<!\d)\d{2,6}(?!\d)", name))
    branch = bool(source_numbers & candidate_numbers)
    source_addresses = {
        token for item in station["addresses"] for token in tokens(item)
    }
    place_addresses = {
        token
        for address in addresses
        for token in tokens(address.get("freeform") or "")
    }
    road_tokens = {
        token for token in source_addresses if token.isdigit() and len(token) > 1
    }
    road_overlap = len(road_tokens & place_addresses)
    score_value = (
        similarity
        + (0.45 if locality else 0)
        + (0.45 if branch else 0)
        + min(0.3, road_overlap * 0.1)
        - (0.6 if city_conflict else 0)
    )
    detail = {
        "place_id": place["id"],
        "name": name,
        "addresses": addresses,
        "point_wkt": place["point_wkt"],
        "category": place["basic_category"],
        "operating_status": place["operating_status"],
        "name_similarity": round(similarity, 3),
        "city_match": locality,
        "city_conflict": city_conflict,
        "branch_number_match": branch,
        "road_token_overlap": road_overlap,
        "score": round(score_value, 3),
        "decision": "unresolved",
    }
    return score_value, detail


def ambiguous_candidates(candidates: list[dict]) -> bool:
    """Flag plausible same-city alternatives far enough apart to be distinct sites."""
    if len(candidates) < 2:
        return False
    best = candidates[0]
    if not best["city_match"] or best["name_similarity"] < 0.7:
        return False
    first = re.fullmatch(r"POINT \((-?[\d.]+) (-?[\d.]+)\)", best["point_wkt"])
    if first is None:
        return True
    for other in candidates[1:]:
        if (
            not other["city_match"]
            or other["name_similarity"] < 0.7
            or best["score"] - other["score"] > 0.25
        ):
            continue
        second = re.fullmatch(r"POINT \((-?[\d.]+) (-?[\d.]+)\)", other["point_wkt"])
        if second is None:
            return True
        latitude_delta = float(first.group(2)) - float(second.group(2))
        longitude_delta = float(first.group(1)) - float(second.group(1))
        if math.hypot(latitude_delta, longitude_delta) > 0.01:
            return True
    return False


def rank_state(code: str) -> dict:
    state = code.upper()
    manifest_path = OUTPUT_ROOT / f"{state.lower()}_manifest.json"
    catalog_path = OUTPUT_ROOT / f"{state.lower()}_places.ndjson"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog_data = catalog_path.read_bytes()
    if (
        manifest["state"] != state
        or manifest["release"] != RELEASE
        or hashlib.sha256(catalog_data).hexdigest() != manifest["output_sha256"]
    ):
        raise ValueError("Overture state catalog differs from its manifest")
    places = [json.loads(line) for line in catalog_data.splitlines()]
    city_index: dict[str, set[int]] = defaultdict(set)
    token_index: dict[str, set[int]] = defaultdict(set)
    branch_index: dict[str, set[int]] = defaultdict(set)
    for index, place in enumerate(places):
        for address in place["addresses"] or []:
            if (address.get("region") or "").upper() == state:
                locality = (address.get("locality") or "").casefold().strip()
                if locality:
                    city_index[locality].add(index)
        for token in tokens(place["name"] or ""):
            token_index[token].add(index)
        for branch in re.findall(r"(?<!\d)\d{2,6}(?!\d)", place["name"] or ""):
            branch_index[branch].add(index)
    queue = build_queue()
    stations = [
        item
        for item in queue["stations"]
        if item["country"] == "US" and state in item["states"]
    ]
    rows = []
    for station in stations:
        pool: set[int] = set()
        for city in station["cities"]:
            pool.update(city_index.get(city.casefold().strip(), ()))
        for name in station["names"]:
            for branch in re.findall(r"#\s*(\d+)", name):
                pool.update(branch_index.get(branch, ()))
            for token in tokens(name):
                if len(token) >= 4 and len(token_index.get(token, ())) <= 500:
                    pool.update(token_index[token])
        ranked = [score(station, places[index], state) for index in pool]
        candidates = [
            detail
            for _, detail in sorted(
                (item for item in ranked if item is not None),
                key=lambda item: (-item[0], item[1]["place_id"]),
            )[:12]
        ]
        rows.append(
            {
                "opis_id": station["opis_id"],
                "names": station["names"],
                "addresses": station["addresses"],
                "cities": station["cities"],
                "state": state,
                "candidates": candidates,
                "needs_address_evidence": ambiguous_candidates(candidates),
                "branch_city_conflict": any(
                    candidate["branch_number_match"] and candidate["city_conflict"]
                    for candidate in candidates
                ),
                "decision": "unresolved",
            }
        )
    review = {
        "status": "candidate_review_only",
        "source_sha256": queue["source_sha256"],
        "candidate_sha256": queue["candidate_sha256"],
        "overture_catalog_sha256": manifest["output_sha256"],
        "overture_release": RELEASE,
        "state": state,
        "unmatched_station_count": len(rows),
        "stations": rows,
    }
    destination = REVIEW_ROOT / f"{state.lower()}_review.json"
    destination.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    arguments = parser.parse_args()
    result = rank_state(arguments.state)
    print(
        f"{result['state']}: {result['unmatched_station_count']} unmatched CSV IDs "
        f"ranked into {REVIEW_ROOT}"
    )


if __name__ == "__main__":
    main()
