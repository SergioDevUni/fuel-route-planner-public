import re

from app.stations.catalogs.overture_context import OvertureContext
from app.stations.catalogs.overture_identity import (
    ALLOWED_CATEGORIES,
    branch_name_similarity,
    branch_number,
    nearby_branch_candidates,
    nearby_city,
    normalized,
    tokens,
)


def match_state_near(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    for opis_id in sorted(context.eligible_ids, key=int):
        station = context.source[opis_id]
        if (
            station["states"] != [context.state]
            or opis_id in context.already_matched
            or any(branch_number(name) for name in station["names"])
        ):
            continue
        possible = {
            place_id: place
            for name in station["names"]
            if len(tokens(name)) >= 2
            for place_id, place in context.name_index.get(normalized(name), {}).items()
        }
        if len(possible) != 1:
            continue
        place = next(iter(possible.values()))
        name_key = normalized(place["name"])
        if (
            context.state_name_count[name_key] != 1
            or place["id"] in context.used_places
            or place["basic_category"] not in ALLOWED_CATEGORIES
            or ((place["confidence"] or 0) < 0.9)
            or (place["operating_status"] not in {None, "open"})
        ):
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        latitude, longitude = (float(point.group(2)), float(point.group(1)))
        city_match = nearby_city(
            context.state,
            station["cities"],
            (latitude, longitude),
            context.centers,
            max_miles=5.0,
        )
        if city_match is None:
            continue
        addresses = [
            address
            for address in place["addresses"] or []
            if address.get("region") == context.state and address.get("country") == "US"
        ]
        if not addresses:
            continue
        address = addresses[0]
        context.records.append(
            {
                "opis_id": opis_id,
                "state": context.state,
                "csv_name": next(
                    name for name in station["names"] if normalized(name) == name_key
                ),
                "csv_city": city_match[0],
                "overture_id": place["id"],
                "overture_name": place["name"],
                "overture_address": address["freeform"],
                "overture_city": address["locality"],
                "overture_category": place["basic_category"],
                "overture_confidence": place["confidence"],
                "road_number_overlap": [],
                "city_center_id": city_match[1],
                "city_center_distance_miles": round(city_match[2], 3),
                "latitude": latitude,
                "longitude": longitude,
                "site_id": f"overture:{place['id']}",
                "rule": "unique_state_name_near_csv_city",
            }
        )
        context.used_places.add(place["id"])


def match_fuzzy_name(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    for opis_id in sorted(context.eligible_ids, key=int):
        station = context.source[opis_id]
        if station["states"] != [context.state] or opis_id in context.already_matched:
            continue
        matches: dict[str, tuple[dict, str, str, float]] = {}
        for source_name in station["names"]:
            number = branch_number(source_name)
            if number is None:
                continue
            for city in station["cities"]:
                possible_places = context.branch_index.get(
                    (number, city.casefold()), {}
                )
                for place in possible_places.values():
                    place_numbers = set(
                        re.findall("(?<!\\d)\\d{2,6}(?!\\d)", place["name"] or "")
                    )
                    similarity = branch_name_similarity(
                        source_name, place["name"] or ""
                    )
                    if place_numbers != {number} or similarity < 0.9:
                        continue
                    matches[place["id"]] = (place, source_name, city, similarity)
        if len(matches) != 1:
            continue
        place, matched_name, matched_city, similarity = next(iter(matches.values()))
        if (
            place["basic_category"] not in ALLOWED_CATEGORIES
            or (place["confidence"] or 0) < 0.8
            or place["operating_status"] not in {None, "open"}
        ):
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        matched_address = next(
            address
            for address in place["addresses"]
            if address.get("region") == context.state
            and address.get("country") == "US"
            and (
                (address.get("locality") or "").casefold().strip()
                == matched_city.casefold()
            )
        )
        context.records.append(
            {
                "opis_id": opis_id,
                "state": context.state,
                "csv_name": matched_name,
                "csv_city": matched_city,
                "overture_id": place["id"],
                "overture_name": place["name"],
                "overture_address": matched_address["freeform"],
                "overture_city": matched_address["locality"],
                "overture_category": place["basic_category"],
                "overture_confidence": place["confidence"],
                "branch_number": branch_number(matched_name),
                "name_similarity": round(similarity, 4),
                "latitude": float(point.group(2)),
                "longitude": float(point.group(1)),
                "site_id": f"overture:{place['id']}",
                "rule": "unique_branch_name_city"
                if place["confidence"] >= 0.9
                else "unique_branch_name_city_strong",
            }
        )


def match_near_branch(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
    for opis_id in sorted(context.eligible_ids, key=int):
        station = context.source[opis_id]
        if station["states"] != [context.state] or opis_id in context.already_matched:
            continue
        branches = {branch_number(name) for name in station["names"]}
        if len(branches) != 1 or None in branches:
            continue
        number = next(iter(branches))
        if number is None:
            continue
        branch_places = list(context.branch_state_index.get(number, {}).values())
        candidates = nearby_branch_candidates(
            branch_places, context.state, station, context.centers
        )
        if len(candidates) != 1:
            continue
        place, source_name, city_match, similarity = candidates[0]
        if place["id"] in context.used_places:
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        address = next(
            address
            for address in place["addresses"]
            if address.get("region") == context.state and address.get("country") == "US"
        )
        context.records.append(
            {
                "opis_id": opis_id,
                "state": context.state,
                "csv_name": source_name,
                "csv_city": city_match[0],
                "overture_id": place["id"],
                "overture_name": place["name"],
                "overture_address": address["freeform"],
                "overture_city": address["locality"],
                "overture_category": place["basic_category"],
                "overture_confidence": place["confidence"],
                "branch_number": number,
                "name_similarity": round(similarity, 4),
                "road_number_overlap": [],
                "city_center_id": city_match[1],
                "city_center_distance_miles": round(city_match[2], 3),
                "latitude": float(point.group(2)),
                "longitude": float(point.group(1)),
                "site_id": f"overture:{place['id']}",
                "rule": "unique_branch_name_near_csv_city",
            }
        )
        context.used_places.add(place["id"])
