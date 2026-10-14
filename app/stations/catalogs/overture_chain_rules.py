import re
from collections import Counter

from app.stations.catalogs.overture_context import OvertureContext
from app.stations.catalogs.overture_identity import (
    chain_name,
    fuzzy_chain_city_matches,
    name_similarity,
    road_numbers,
    unique_chain_city_matches,
)


def match_chain(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
    context.csv_city_counts = Counter(
        (city.casefold().strip(), chain_name(name))
        for station in context.source.values()
        if station["states"] == [context.state]
        for city in station["cities"]
        for name in {chain_name(name) for name in station["names"]}
        if name
    )
    chain_proposals = []
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
        matches = unique_chain_city_matches(
            station, context.state, list(possible_places.values())
        )
        if len(matches) != 1:
            continue
        place, address = next(iter(matches.values()))
        csv_city = next(
            (
                city
                for city in station["cities"]
                if city.casefold().strip() == address["locality"].casefold().strip()
            ),
            None,
        )
        csv_name = next(
            (
                name
                for name in station["names"]
                if chain_name(name) == chain_name(place["name"] or "")
            ),
            None,
        )
        if (
            csv_city is None
            or csv_name is None
            or context.csv_city_counts[
                csv_city.casefold().strip(), chain_name(csv_name)
            ]
            != 1
            or (place["id"] in context.used_places)
            or (
                context.occupied_sites.get(f"overture:{place['id']}", opis_id)
                != opis_id
            )
        ):
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is not None:
            chain_proposals.append((opis_id, place, address, csv_name, csv_city, point))
    proposal_counts = Counter(item[1]["id"] for item in chain_proposals)
    for opis_id, place, address, csv_name, csv_city, point in chain_proposals:
        if proposal_counts[place["id"]] != 1:
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
                "name_similarity": round(name_similarity([csv_name], place["name"]), 4),
                "latitude": float(point.group(2)),
                "longitude": float(point.group(1)),
                "site_id": f"overture:{place['id']}",
                "rule": "unique_chain_name_city",
            }
        )


def match_fuzzy_chain(context: OvertureContext) -> None:
    context.already_matched = {item["opis_id"] for item in context.records}
    context.used_places = {item["overture_id"] for item in context.records}
    fuzzy_proposals = []
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
        matches = fuzzy_chain_city_matches(
            station, context.state, list(possible_places.values())
        )
        if len(matches) != 1:
            continue
        place, address, csv_name, similarity = next(iter(matches.values()))
        csv_city = next(
            (
                city
                for city in station["cities"]
                if city.casefold().strip() == address["locality"].casefold().strip()
            ),
            None,
        )
        if (
            csv_city is None
            or context.csv_city_counts[
                csv_city.casefold().strip(), chain_name(csv_name)
            ]
            != 1
            or place["id"] in context.used_places
            or (
                context.occupied_sites.get(f"overture:{place['id']}", opis_id)
                != opis_id
            )
        ):
            continue
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if point is not None:
            fuzzy_proposals.append(
                (opis_id, place, address, csv_name, csv_city, similarity, point)
            )
    proposal_counts = Counter(item[1]["id"] for item in fuzzy_proposals)
    for (
        opis_id,
        place,
        address,
        csv_name,
        csv_city,
        similarity,
        point,
    ) in fuzzy_proposals:
        if proposal_counts[place["id"]] != 1:
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
                "rule": "unique_fuzzy_chain_city",
            }
        )
