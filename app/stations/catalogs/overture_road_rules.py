import re
from collections import Counter

from app.stations.catalogs.overture_context import OvertureContext
from app.stations.catalogs.overture_identity import (
    csv_distinctive_city_matches,
    csv_road_site_matches,
    name_similarity,
    road_numbers,
)


def match_csv_road(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
    proposals: list[tuple[str, dict, dict, float, str, str]] = []
    for opis_id in sorted(context.eligible_ids, key=int):
        station = context.source[opis_id]
        if station["states"] != [context.state] or opis_id in context.already_matched:
            continue
        possible_places = {
            place_id: place
            for city in station["cities"]
            for place_id, place in context.city_places.get(
                city.casefold().strip(), {}
            ).items()
        }
        matches = csv_road_site_matches(
            station, context.state, list(possible_places.values())
        )
        if len(matches) != 1:
            continue
        place, address, similarity = next(iter(matches.values()))
        if (
            place["id"] in context.used_places
            or context.occupied_sites.get(f"overture:{place['id']}", opis_id) != opis_id
        ):
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        csv_name = max(
            station["names"],
            key=lambda name: name_similarity([name], place["name"] or ""),
        )
        csv_city = next(
            city
            for city in station["cities"]
            if city.casefold().strip() == address["locality"].casefold().strip()
        )
        proposals.append((opis_id, place, address, similarity, csv_name, csv_city))
    proposal_counts = Counter(item[1]["id"] for item in proposals)
    for opis_id, place, address, similarity, csv_name, csv_city in proposals:
        if proposal_counts[place["id"]] != 1:
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        csv_roads = set().union(
            *(road_numbers(value) for value in context.source[opis_id]["addresses"])
        )
        context.records.append(
            {
                "opis_id": opis_id,
                "state": context.state,
                "csv_name": csv_name,
                "csv_city": csv_city,
                "overture_id": place["id"],
                "overture_name": place["name"],
                "overture_address": address["freeform"],
                "overture_city": address["locality"],
                "overture_category": place["basic_category"],
                "overture_confidence": place["confidence"],
                "road_number_overlap": sorted(
                    csv_roads & road_numbers(address["freeform"] or "")
                ),
                "name_similarity": similarity,
                "latitude": float(point.group(2)),
                "longitude": float(point.group(1)),
                "site_id": f"overture:{place['id']}",
                "rule": "unique_csv_name_road_city",
            }
        )


def match_distinctive(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
    distinctive_proposals: list[tuple[str, dict, dict, float, str, str]] = []
    for opis_id in sorted(context.eligible_ids, key=int):
        station = context.source[opis_id]
        if station["states"] != [context.state] or opis_id in context.already_matched:
            continue
        possible_places = {
            place_id: place
            for city in station["cities"]
            for place_id, place in context.city_places.get(
                city.casefold().strip(), {}
            ).items()
        }
        matches = csv_distinctive_city_matches(
            station, context.state, list(possible_places.values())
        )
        if len(matches) != 1:
            continue
        place, address, similarity = next(iter(matches.values()))
        if (
            place["id"] in context.used_places
            or context.occupied_sites.get(f"overture:{place['id']}", opis_id) != opis_id
        ):
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        csv_name = max(
            station["names"],
            key=lambda name: name_similarity([name], place["name"] or ""),
        )
        csv_city = next(
            city
            for city in station["cities"]
            if city.casefold().strip() == address["locality"].casefold().strip()
        )
        distinctive_proposals.append(
            (opis_id, place, address, similarity, csv_name, csv_city)
        )
    proposal_counts = Counter(item[1]["id"] for item in distinctive_proposals)
    for (
        opis_id,
        place,
        address,
        similarity,
        csv_name,
        csv_city,
    ) in distinctive_proposals:
        if proposal_counts[place["id"]] != 1:
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is None:
            continue
        csv_roads = set().union(
            *(road_numbers(value) for value in context.source[opis_id]["addresses"])
        )
        context.records.append(
            {
                "opis_id": opis_id,
                "state": context.state,
                "csv_name": csv_name,
                "csv_city": csv_city,
                "overture_id": place["id"],
                "overture_name": place["name"],
                "overture_address": address["freeform"],
                "overture_city": address["locality"],
                "overture_category": place["basic_category"],
                "overture_confidence": place["confidence"],
                "road_number_overlap": sorted(
                    csv_roads & road_numbers(address["freeform"] or "")
                ),
                "name_similarity": similarity,
                "latitude": float(point.group(2)),
                "longitude": float(point.group(1)),
                "site_id": f"overture:{place['id']}",
                "rule": "unique_distinctive_name_local_site",
            }
        )
