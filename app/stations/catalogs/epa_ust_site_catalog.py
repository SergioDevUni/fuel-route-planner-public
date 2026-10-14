"""Shared epa ust site catalog rules used by review and preparation tools."""

import hashlib
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from app.stations.catalogs.epa_sources import OUTPUT_ROOT, SERVICE, STATE_NAMES
from app.stations.catalogs.names import tokens
from app.stations.catalogs.overture_identity import (
    CITY_PLACES,
    branch_number,
    chain_name,
    city_centers,
    nearby_city,
    road_numbers,
    source_stations,
)
from app.stations.catalogs.paths import SOURCE as SOURCE


def normalized(value: str | None) -> str:
    return " ".join(sorted(tokens(value or "")))


def city_key(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def facility_numbers(value: str | None) -> set[str]:
    return set(re.findall(r"(?<!\d)\d{3,7}(?!\d)", value or ""))


def brand_similarity(source_name: str, facility_name: str) -> float:
    def words(value: str) -> str:
        return " ".join(sorted(re.findall(r"[a-z]+", value.casefold())))

    return SequenceMatcher(None, words(source_name), words(facility_name)).ratio()


def same_brand_prefix(source_name: str, facility_name: str) -> bool:
    """Accept a shared distinctive brand before generic site descriptors."""
    source = re.findall(r"[a-z0-9]+", source_name.casefold())
    facility = re.findall(r"[a-z0-9]+", facility_name.casefold())
    if not source or not facility:
        return False
    if source[:2] == facility[:2] and source[0] in {"7", "tex"}:
        return True
    generic = {"fuel", "gas", "truck", "travel", "service", "store", "quick", "fast"}
    return len(source[0]) >= 4 and source[0] not in generic and source[0] == facility[0]


def usable_coordinate(row: dict) -> bool:
    latitude, longitude = row["Latitude"], row["Longitude"]
    return (
        isinstance(latitude, (int, float))
        and isinstance(longitude, (int, float))
        and 24 <= latitude <= 50
        and -125 <= longitude <= -66
        and row["Address_Match_Type"]
        in {None, "PointAddress", "StreetAddress", "StreetInt", "StreetAddressExt"}
        and row["Coordinate_Source"] in {"State", "Geocode", "GPS_EPA"}
    )


def load_state(code: str) -> tuple[list[dict], str]:
    path = OUTPUT_ROOT / f"{code.lower()}_facilities.ndjson"
    manifest_path = OUTPUT_ROOT / f"{code.lower()}_manifest.json"
    raw = path.read_bytes()
    manifest = json.loads(manifest_path.read_bytes())
    if (
        manifest["source_url"] != SERVICE
        or manifest["state_code"] != code
        or manifest["state_name"] != STATE_NAMES[code]
        or manifest["sha256"] != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError(f"EPA source changed for {code}")
    rows = [json.loads(line) for line in raw.splitlines()]
    if len(rows) != manifest["count"] or len({r["OBJECTID"] for r in rows}) != len(
        rows
    ):
        raise ValueError(f"EPA extract incomplete for {code}")
    return rows, manifest["sha256"]


def state_index(rows: list[dict]) -> tuple[dict, dict, Counter, dict, dict, dict, dict]:
    names: dict[tuple[str, str], dict[int, dict]] = defaultdict(dict)
    branches: dict[tuple[str, str], dict[int, dict]] = defaultdict(dict)
    state_names: dict[str, dict[int, dict]] = defaultdict(dict)
    state_branches: dict[str, dict[int, dict]] = defaultdict(dict)
    counts: Counter = Counter()
    chains: dict[tuple[str, str], dict[int, dict]] = defaultdict(dict)
    city_facilities: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        city = city_key(row["City"])
        name = normalized(row["Name"])
        if not name:
            continue
        state_names[name][row["OBJECTID"]] = row
        if city:
            names[(city, name)][row["OBJECTID"]] = row
            counts[name] += 1
            city_facilities[city][row["OBJECTID"]] = row
            if chain_name(row["Name"] or ""):
                chains[(city, chain_name(row["Name"]))][row["OBJECTID"]] = row
        for number in facility_numbers(row["Name"]):
            state_branches[number][row["OBJECTID"]] = row
            if city:
                branches[(city, number)][row["OBJECTID"]] = row
    return names, branches, counts, state_names, state_branches, chains, city_facilities


def csv_chain_city_counts(stations: dict[str, dict]) -> Counter:
    return Counter(
        (state, city_key(city), base)
        for station in stations.values()
        if len(station["states"]) == 1
        for state in station["states"]
        for city in station["cities"]
        for base in {chain_name(name) for name in station["names"]}
        if base
    )


def choose_chain_city(
    station: dict, state: str, chains: dict, csv_counts: Counter
) -> tuple | None:
    matches = {
        object_id: (row, csv_name, csv_city)
        for csv_city in station["cities"]
        for csv_name in station["names"]
        if csv_counts[(state, city_key(csv_city), chain_name(csv_name))] == 1
        for object_id, row in chains.get(
            (city_key(csv_city), chain_name(csv_name)), {}
        ).items()
        if usable_coordinate(row)
        and row["Facility_Status"] == "Open UST(s)"
        and (
            not re.findall(r"#\s*(\d{1,7})\b", row["Name"] or "")
            or {int(n) for n in re.findall(r"#\s*(\d{1,7})\b", row["Name"])}
            == {int(n) for n in re.findall(r"#\s*(\d{1,7})\b", csv_name)}
        )
    }
    if len(matches) != 1:
        return None
    row, csv_name, csv_city = next(iter(matches.values()))
    overlap = sorted(
        set().union(*(road_numbers(a) for a in station["addresses"]))
        & road_numbers(row["Address"] or "")
    )
    return row, csv_name, csv_city, "unique_chain_city", overlap, 0.0


def choose_fuzzy_chain_city(
    station: dict, state: str, city_facilities: dict, csv_counts: Counter
) -> tuple | None:
    generic = {"mini", "mart", "market", "quick", "express", "stopping", "circle"}
    matches = {}
    for csv_city in station["cities"]:
        for row in city_facilities.get(city_key(csv_city), {}).values():
            if not usable_coordinate(row) or row["Facility_Status"] != "Open UST(s)":
                continue
            if re.match(r"^UPS\b", row["Name"] or "", flags=re.IGNORECASE):
                continue
            mapped_base = chain_name(row["Name"] or "")
            for csv_name in station["names"]:
                base = chain_name(csv_name)
                if csv_counts[(state, city_key(csv_city), base)] != 1:
                    continue
                shared = {word for word in base.split() if len(word) >= 4} & {
                    word for word in mapped_base.split() if len(word) >= 4
                }
                similarity = SequenceMatcher(None, base, mapped_base).ratio()
                source_branch = {
                    int(n) for n in re.findall(r"#\s*(\d{1,7})\b", csv_name)
                }
                mapped_branch = {
                    int(n) for n in re.findall(r"#\s*(\d{1,7})\b", row["Name"] or "")
                }
                source_trailing = re.search(r"(?:#\s*|\b)(\d{1,7})\s*$", csv_name)
                mapped_trailing = re.search(
                    r"(?:#\s*|\b)(\d{1,7})\s*$", row["Name"] or ""
                )
                directions = {"north", "south", "east", "west"}
                source_directions = set(base.split()) & directions
                mapped_directions = set(mapped_base.split()) & directions
                if (
                    similarity < 0.8
                    or not shared - generic
                    or (mapped_branch and mapped_branch != source_branch)
                    or (
                        source_trailing is not None
                        and mapped_trailing is not None
                        and int(source_trailing.group(1))
                        != int(mapped_trailing.group(1))
                    )
                    or (
                        source_directions
                        and mapped_directions
                        and source_directions != mapped_directions
                    )
                ):
                    continue
                matches[row["OBJECTID"]] = (row, csv_name, csv_city)
    if len(matches) != 1:
        return None
    row, csv_name, csv_city = next(iter(matches.values()))
    overlap = sorted(
        set().union(*(road_numbers(a) for a in station["addresses"]))
        & road_numbers(row["Address"] or "")
    )
    return row, csv_name, csv_city, "unique_fuzzy_chain_city", overlap, 0.0


def choose(station: dict, names: dict, branches: dict, counts: Counter) -> tuple | None:
    exact = {
        object_id: (row, csv_name, csv_city)
        for csv_name in station["names"]
        for csv_city in station["cities"]
        for object_id, row in names.get(
            (city_key(csv_city), normalized(csv_name)), {}
        ).items()
    }
    if len(exact) == 1:
        row, csv_name, csv_city = next(iter(exact.values()))
        csv_roads = set().union(
            *(road_numbers(value) for value in station["addresses"])
        )
        overlap = sorted(csv_roads & road_numbers(row["Address"] or ""))
        number = branch_number(csv_name)
        if usable_coordinate(row) and (
            (number is not None and number in facility_numbers(row["Name"]))
            or (len(tokens(csv_name)) >= 2 and counts[normalized(csv_name)] <= 3)
            or overlap
        ):
            return row, csv_name, csv_city, "exact_name_city", overlap
    numbers = {branch_number(name) for name in station["names"]}
    if len(numbers) != 1 or None in numbers:
        return None
    number = next(iter(numbers))
    if number is None or len(number) < 3:
        return None
    matched = {
        object_id: (row, csv_name, csv_city)
        for csv_name in station["names"]
        for csv_city in station["cities"]
        for object_id, row in branches.get((city_key(csv_city), number), {}).items()
    }
    if len(matched) != 1:
        return None
    row, csv_name, csv_city = next(iter(matched.values()))
    if not usable_coordinate(row) or not (
        brand_similarity(csv_name, row["Name"]) >= 0.55
        or same_brand_prefix(csv_name, row["Name"])
    ):
        return None
    return row, csv_name, csv_city, "branch_city", []


def choose_nearby(
    station: dict,
    state: str,
    state_names: dict,
    state_branches: dict,
    centers: dict,
) -> tuple | None:
    branches = {branch_number(name) for name in station["names"]}
    branches.discard(None)
    if len(branches) == 1:
        number = next(iter(branches))
        candidates = {
            row["OBJECTID"]: (row, csv_name, city_match)
            for row in state_branches.get(number, {}).values()
            if usable_coordinate(row)
            for csv_name in station["names"]
            if branch_number(csv_name) == number
            and brand_similarity(csv_name, row["Name"]) >= 0.55
            for city_match in [
                nearby_city(
                    state,
                    station["cities"],
                    (row["Latitude"], row["Longitude"]),
                    centers,
                    max_miles=10.0,
                )
            ]
            if city_match is not None
        }
        if len(candidates) == 1:
            row, csv_name, city_match = next(iter(candidates.values()))
            return (
                row,
                csv_name,
                city_match[0],
                "branch_nearby_city",
                [],
                round(city_match[2], 3),
            )
    candidates = {
        row["OBJECTID"]: (row, csv_name, city_match)
        for csv_name in station["names"]
        if len(tokens(csv_name)) >= 2
        for row in state_names.get(normalized(csv_name), {}).values()
        if usable_coordinate(row)
        for city_match in [
            nearby_city(
                state,
                station["cities"],
                (row["Latitude"], row["Longitude"]),
                centers,
                max_miles=10.0,
            )
        ]
        if city_match is not None
    }
    if len(candidates) != 1:
        return None
    row, csv_name, city_match = next(iter(candidates.values()))
    csv_roads = set().union(*(road_numbers(value) for value in station["addresses"]))
    overlap = sorted(csv_roads & road_numbers(row["Address"] or ""))
    if not overlap and len(state_names[normalized(csv_name)]) > 3:
        return None
    return (
        row,
        csv_name,
        city_match[0],
        "name_nearby_city",
        overlap,
        round(city_match[2], 3),
    )


def select(
    station: dict,
    state: str,
    indices: tuple[dict, dict, Counter, dict, dict, dict, dict],
    centers: dict,
    csv_counts: Counter,
) -> tuple | None:
    names, branches, counts, state_names, state_branches, chains, city_facilities = (
        indices
    )
    same_city = choose(station, names, branches, counts)
    if same_city is not None:
        return (*same_city, 0.0)
    nearby = choose_nearby(station, state, state_names, state_branches, centers)
    if nearby is not None:
        return nearby
    chain = choose_chain_city(station, state, chains, csv_counts)
    return (
        chain
        if chain is not None
        else choose_fuzzy_chain_city(station, state, city_facilities, csv_counts)
    )


def validate_catalog(document: dict) -> None:
    if (
        document["source_sha256"] != hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        or document["epa_source_url"] != SERVICE
        or document["city_catalog_sha256"]
        != hashlib.sha256(CITY_PLACES.read_bytes()).hexdigest()
        or document["verified_count"] != len(document["records"])
    ):
        raise ValueError("EPA site catalog source changed")
    stations = source_stations()
    csv_counts = csv_chain_city_counts(stations)
    centers = city_centers()
    used_ids: set[str] = set()
    used_facilities: set[int] = set()
    by_state: dict[str, list[dict]] = defaultdict(list)
    for record in document["records"]:
        by_state[record["state"]].append(record)
    for code, records in by_state.items():
        rows, source_hash = load_state(code)
        if source_hash != document["state_hashes"][code]:
            raise ValueError(f"EPA catalog source hash changed for {code}")
        indices = state_index(rows)
        for record in records:
            opis_id = record["opis_id"]
            station = stations.get(opis_id)
            chosen = (
                select(station, code, indices, centers, csv_counts) if station else None
            )
            if chosen is None or station["states"] != [code]:
                raise ValueError(f"EPA CSV identity changed for {opis_id}")
            row, csv_name, csv_city, rule, overlap, distance = chosen
            if (
                opis_id in used_ids
                or row["OBJECTID"] in used_facilities
                or record["csv_name"] != csv_name
                or record["csv_city"] != csv_city
                or record["rule"] != rule
                or record["road_number_overlap"] != overlap
                or record["city_distance_miles"] != distance
                or record["epa_object_id"] != row["OBJECTID"]
                or record["epa_facility_id"] != row["Facility_ID"]
                or record["epa_name"] != row["Name"]
                or record["epa_address"] != row["Address"]
                or record["epa_city"] != row["City"]
                or record["epa_status"] != row["Facility_Status"]
                or record["coordinate_source"] != row["Coordinate_Source"]
                or record["address_match_type"] != row["Address_Match_Type"]
                or (record["latitude"], record["longitude"])
                != (row["Latitude"], row["Longitude"])
                or record["site_id"] != f"epa-ust:{row['OBJECTID']}"
            ):
                raise ValueError(f"EPA site link changed for {opis_id}")
            used_ids.add(opis_id)
            used_facilities.add(row["OBJECTID"])
