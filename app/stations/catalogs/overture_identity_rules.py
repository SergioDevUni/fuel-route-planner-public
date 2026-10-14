import re

from app.stations.catalogs.overture_context import OvertureContext
from app.stations.catalogs.overture_identity import (
    ALLOWED_CATEGORIES,
    branch_number,
    nearby_city,
    normalized,
    road_numbers,
    tokens,
)


def match_exact_city(context: OvertureContext) -> None:
    for opis_id in sorted(context.eligible_ids, key=int):
        station = context.source[opis_id]
        if station["states"] != [context.state]:
            continue
        matched: dict[str, dict] = {}
        matched_name = ""
        matched_city = ""
        for name in station["names"]:
            for city in station["cities"]:
                for place_id, place in context.index.get(
                    (normalized(name), city.casefold()), {}
                ).items():
                    matched[place_id] = place
                    matched_name, matched_city = (name, city)
        if len(matched) != 1:
            continue
        place = next(iter(matched.values()))
        if (
            len(tokens(matched_name)) < 2
            or place["basic_category"] not in ALLOWED_CATEGORIES
            or (place["confidence"] or 0) < 0.9
            or (place["operating_status"] not in {None, "open"})
        ):
            continue
        name_key = normalized(matched_name)
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
        csv_roads = set().union(
            *(road_numbers(address) for address in station["addresses"])
        )
        place_roads = road_numbers(matched_address.get("freeform") or "")
        if context.state_name_count[name_key] > 2 and (not csv_roads & place_roads):
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
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
                "name_occurrences_state": context.state_name_count[name_key],
                "road_number_overlap": sorted(csv_roads & place_roads),
                "latitude": float(point.group(2)),
                "longitude": float(point.group(1)),
                "site_id": f"overture:{place['id']}",
                "rule": "unique_exact_name_city_distinctive",
            }
        )


def match_state_road(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
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
            or ((place["confidence"] or 0) < 0.8)
            or (place["operating_status"] not in {None, "open"})
        ):
            continue
        csv_roads = set().union(
            *(road_numbers(address) for address in station["addresses"])
        )
        matching_addresses = [
            address
            for address in place["addresses"] or []
            if address.get("region") == context.state
            and address.get("country") == "US"
            and csv_roads & road_numbers(address.get("freeform") or "")
        ]
        if not matching_addresses:
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        latitude, longitude = (float(point.group(2)), float(point.group(1)))
        city_match = nearby_city(
            context.state, station["cities"], (latitude, longitude), context.centers
        )
        if city_match is None:
            continue
        address = matching_addresses[0]
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
                "road_number_overlap": sorted(
                    csv_roads & road_numbers(address["freeform"] or "")
                ),
                "city_center_id": city_match[1],
                "city_center_distance_miles": round(city_match[2], 3),
                "latitude": latitude,
                "longitude": longitude,
                "site_id": f"overture:{place['id']}",
                "rule": "unique_state_name_road_near_csv_city",
            }
        )
        context.used_places.add(place["id"])


def match_long_branch(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
    for opis_id in sorted(context.eligible_ids, key=int):
        station = context.source[opis_id]
        if station["states"] != [context.state] or opis_id in context.already_matched:
            continue
        numbers = {branch_number(name) for name in station["names"]}
        if len(numbers) != 1 or None in numbers:
            continue
        number = next(iter(numbers))
        if number is None or not 4 <= len(number) <= 7:
            continue
        matches = {
            place_id: (place, city)
            for city in station["cities"]
            for place_id, place in context.branch_index.get(
                (number, city.casefold()), {}
            ).items()
            if place["basic_category"] in ALLOWED_CATEGORIES
            and (place["confidence"] or 0) >= 0.75
            and (place["operating_status"] in {None, "open"})
            and (number in re.findall("(?<!\\d)\\d{4,7}(?!\\d)", place["name"] or ""))
        }
        if len(matches) != 1:
            continue
        place, city = next(iter(matches.values()))
        if place["id"] in context.used_places:
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        address = next(
            address
            for address in place["addresses"] or []
            if address.get("region") == context.state
            and address.get("country") == "US"
            and ((address.get("locality") or "").casefold() == city.casefold())
        )
        context.records.append(
            {
                "opis_id": opis_id,
                "state": context.state,
                "csv_name": next(
                    name for name in station["names"] if branch_number(name) == number
                ),
                "csv_city": city,
                "overture_id": place["id"],
                "overture_name": place["name"],
                "overture_address": address["freeform"],
                "overture_city": address["locality"],
                "overture_category": place["basic_category"],
                "overture_confidence": place["confidence"],
                "branch_number": number,
                "latitude": float(point.group(2)),
                "longitude": float(point.group(1)),
                "site_id": f"overture:{place['id']}",
                "rule": "unique_long_branch_city",
            }
        )
        context.used_places.add(place["id"])


def match_city_road(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
    for opis_id in sorted(context.eligible_ids, key=int):
        station = context.source[opis_id]
        if station["states"] != [context.state] or opis_id in context.already_matched:
            continue
        matches = {
            place_id: (place, name, city)
            for name in station["names"]
            for city in station["cities"]
            for place_id, place in context.index.get(
                (normalized(name), city.casefold()), {}
            ).items()
        }
        if len(matches) != 1:
            continue
        place, name, city = next(iter(matches.values()))
        if (
            place["id"] in context.used_places
            or place["basic_category"] not in ALLOWED_CATEGORIES
            or (place["confidence"] or 0) < 0.75
            or (place["operating_status"] not in {None, "open"})
        ):
            continue
        address = next(
            address
            for address in place["addresses"] or []
            if address.get("region") == context.state
            and address.get("country") == "US"
            and ((address.get("locality") or "").casefold() == city.casefold())
        )
        csv_roads = set().union(
            *(road_numbers(value) for value in station["addresses"])
        )
        overlap = sorted(csv_roads & road_numbers(address["freeform"] or ""))
        occurrences = context.state_name_count[normalized(name)]
        if overlap:
            rule = "unique_name_city_road"
        elif occurrences <= 3 and len(tokens(name)) >= 2:
            rule = "unique_distinctive_name_city"
        else:
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        context.records.append(
            {
                "opis_id": opis_id,
                "state": context.state,
                "csv_name": name,
                "csv_city": city,
                "overture_id": place["id"],
                "overture_name": place["name"],
                "overture_address": address["freeform"],
                "overture_city": address["locality"],
                "overture_category": place["basic_category"],
                "overture_confidence": place["confidence"],
                "name_occurrences_state": occurrences,
                "road_number_overlap": overlap,
                "latitude": float(point.group(2)),
                "longitude": float(point.group(1)),
                "site_id": f"overture:{place['id']}",
                "rule": rule,
            }
        )
        context.used_places.add(place["id"])
