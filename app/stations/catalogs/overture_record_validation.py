import re
from dataclasses import dataclass

from app.stations.catalogs.overture_identity import (
    ALLOWED_CATEGORIES,
    NEARBY_CITY_MILES,
    branch_number,
    chain_name,
    csv_distinctive_city_matches,
    csv_road_site_matches,
    fuzzy_chain_city_matches,
    nearby_branch_candidates,
    nearby_city,
    normalized,
    road_numbers,
    tokens,
    unique_chain_city_matches,
)


@dataclass
class CatalogValidationContext:
    document: dict
    source: dict
    centers: dict
    state_places: dict
    long_branch_places: dict
    road_site_places_by_city: dict
    ids: set[str]


def validate_identity(record: dict, context: CatalogValidationContext) -> None:
    opis_id, state = record["opis_id"], record["state"]
    if (
        opis_id in context.ids
        or state not in context.document["state_catalogs"]
        or context.source.get(opis_id) is None
        or (context.source[opis_id]["states"] != [state])
        or (
            normalized(record["csv_name"])
            not in {normalized(name) for name in context.source[opis_id]["names"]}
        )
        or (
            record["csv_city"].casefold()
            not in {city.casefold() for city in context.source[opis_id]["cities"]}
        )
        or (
            record["rule"]
            not in {
                "unique_exact_name_city_distinctive",
                "unique_branch_name_city",
                "unique_branch_name_city_strong",
                "unique_state_name_road_near_csv_city",
                "unique_state_name_near_csv_city",
                "unique_branch_name_near_csv_city",
                "unique_long_branch_city",
                "unique_name_city_road",
                "unique_distinctive_name_city",
                "unique_csv_name_road_city",
                "unique_distinctive_name_local_site",
                "unique_chain_name_city",
                "unique_fuzzy_chain_city",
            }
        )
    ):
        raise ValueError(f"invalid exact-site CSV identity {opis_id}")


def validate_nearby(record: dict, context: CatalogValidationContext) -> None:
    opis_id, state = record["opis_id"], record["state"]
    if record["rule"] in {
        "unique_state_name_road_near_csv_city",
        "unique_state_name_near_csv_city",
        "unique_branch_name_near_csv_city",
    }:
        places = context.state_places[state]
        source_station = context.source[opis_id]
        if record["rule"] == "unique_branch_name_near_csv_city":
            branch_matches = nearby_branch_candidates(
                places, state, source_station, context.centers
            )
            matches = [item[0] for item in branch_matches]
        else:
            branch_matches = []
            matches = [
                place
                for place in places
                if normalized(place["name"] or "") == normalized(record["csv_name"])
            ]
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)",
            matches[0]["point_wkt"] if len(matches) == 1 else "",
        )
        match = matches[0] if len(matches) == 1 else None
        roads = set().union(
            *(road_numbers(address) for address in source_station["addresses"])
        )
        place_roads = {
            number
            for address in (match["addresses"] or [] if match else [])
            if address.get("region") == state and address.get("country") == "US"
            for number in road_numbers(address.get("freeform") or "")
        }
        city_match = nearby_city(
            state,
            source_station["cities"],
            (record["latitude"], record["longitude"]),
            context.centers,
            max_miles=5.0
            if record["rule"] == "unique_state_name_near_csv_city"
            else 8.0
            if record["rule"] == "unique_branch_name_near_csv_city"
            else NEARBY_CITY_MILES,
        )
        if (
            match is None
            or point is None
            or match["id"] != record["overture_id"]
            or (match["name"] != record["overture_name"])
            or (match["basic_category"] not in ALLOWED_CATEGORIES)
            or (
                (match["confidence"] or 0)
                < (
                    0.8
                    if record["rule"]
                    in {
                        "unique_state_name_road_near_csv_city",
                        "unique_branch_name_near_csv_city",
                    }
                    else 0.9
                )
            )
            or (match["operating_status"] not in {None, "open"})
            or (
                (float(point.group(2)), float(point.group(1)))
                != (record["latitude"], record["longitude"])
            )
            or (
                record["rule"] == "unique_state_name_road_near_csv_city"
                and (
                    not roads & place_roads
                    or record["road_number_overlap"] != sorted(roads & place_roads)
                )
            )
            or (
                record["rule"] == "unique_state_name_near_csv_city"
                and (
                    len(tokens(record["csv_name"])) < 2 or record["road_number_overlap"]
                )
            )
            or (
                record["rule"] == "unique_branch_name_near_csv_city"
                and (
                    len(branch_matches) != 1
                    or branch_matches[0][1] != record["csv_name"]
                    or record["branch_number"] != branch_number(record["csv_name"])
                    or (record["name_similarity"] != round(branch_matches[0][3], 4))
                    or record["road_number_overlap"]
                )
            )
            or (city_match is None)
            or (record["csv_city"] != city_match[0])
            or (record["city_center_id"] != city_match[1])
            or (record["city_center_distance_miles"] != round(city_match[2], 3))
        ):
            raise ValueError(f"nearby-city match differs: {opis_id}")


def validate_long_branch(record: dict, context: CatalogValidationContext) -> None:
    opis_id, state = record["opis_id"], record["state"]
    if record["rule"] == "unique_long_branch_city":
        place = context.long_branch_places[state].get(record["overture_id"])
        if place is None:
            raise ValueError(f"long branch point missing: {opis_id}")
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        number = branch_number(record["csv_name"])
        if (
            number is None
            or len(number) < 4
            or len(number) > 7
            or (number != record["branch_number"])
            or (
                number not in re.findall("(?<!\\d)\\d{4,7}(?!\\d)", place["name"] or "")
            )
            or (place["name"] != record["overture_name"])
            or (place["basic_category"] not in ALLOWED_CATEGORIES)
            or ((place["confidence"] or 0) < 0.75)
            or (place["operating_status"] not in {None, "open"})
            or (point is None)
            or (
                (float(point.group(2)), float(point.group(1)))
                != (record["latitude"], record["longitude"])
            )
            or (
                not any(
                    address.get("region") == state
                    and address.get("country") == "US"
                    and (
                        (address.get("locality") or "").casefold()
                        == record["csv_city"].casefold()
                    )
                    and (address.get("freeform") == record["overture_address"])
                    for address in place["addresses"] or []
                )
            )
        ):
            raise ValueError(f"long branch evidence differs: {opis_id}")


def validate_road_site(record: dict, context: CatalogValidationContext) -> None:
    opis_id, state = record["opis_id"], record["state"]
    if record["rule"] in {
        "unique_csv_name_road_city",
        "unique_distinctive_name_local_site",
    }:
        places = list(
            {
                place_id: place
                for city in context.source[opis_id]["cities"]
                for place_id, place in context.road_site_places_by_city[state]
                .get(city.casefold().strip(), {})
                .items()
            }.values()
        )
        matches = (
            csv_road_site_matches(context.source[opis_id], state, places)
            if record["rule"] == "unique_csv_name_road_city"
            else csv_distinctive_city_matches(context.source[opis_id], state, places)
        )
        if set(matches) != {record["overture_id"]}:
            raise ValueError(f"CSV road-site evidence changed: {opis_id}")
        place, address, similarity = matches[record["overture_id"]]
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        overlap = sorted(
            set().union(
                *(road_numbers(value) for value in context.source[opis_id]["addresses"])
            )
            & road_numbers(address.get("freeform") or "")
        )
        if (
            point is None
            or place["name"] != record["overture_name"]
            or address["freeform"] != record["overture_address"]
            or (address["locality"] != record["overture_city"])
            or (record["road_number_overlap"] != overlap)
            or (record["name_similarity"] != similarity)
            or (
                (float(point.group(2)), float(point.group(1)))
                != (record["latitude"], record["longitude"])
            )
        ):
            raise ValueError(f"CSV road-site point changed: {opis_id}")


def validate_chain(record: dict, context: CatalogValidationContext) -> None:
    opis_id, state = record["opis_id"], record["state"]
    if record["rule"] in {"unique_chain_name_city", "unique_fuzzy_chain_city"}:
        csv_city = record["csv_city"].casefold().strip()
        base = chain_name(record["csv_name"])
        competing_csv = {
            other_id
            for other_id, other in context.source.items()
            if other["states"] == [state]
            and csv_city in {city.casefold().strip() for city in other["cities"]}
            and (base in {chain_name(name) for name in other["names"]})
        }
        places = list(
            context.road_site_places_by_city[state].get(csv_city, {}).values()
        )
        matches = (
            unique_chain_city_matches(context.source[opis_id], state, places)
            if record["rule"] == "unique_chain_name_city"
            else fuzzy_chain_city_matches(context.source[opis_id], state, places)
        )
        if competing_csv != {opis_id} or set(matches) != {record["overture_id"]}:
            raise ValueError(f"chain-city identity changed: {opis_id}")
        place, address = matches[record["overture_id"]][:2]
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        if (
            point is None
            or place["name"] != record["overture_name"]
            or address["freeform"] != record["overture_address"]
            or (address["locality"] != record["overture_city"])
            or (
                record["rule"] == "unique_fuzzy_chain_city"
                and (
                    record["csv_name"] != matches[record["overture_id"]][2]
                    or record["name_similarity"] != matches[record["overture_id"]][3]
                )
            )
            or (
                record["road_number_overlap"]
                != sorted(
                    set().union(
                        *(road_numbers(a) for a in context.source[opis_id]["addresses"])
                    )
                    & road_numbers(address["freeform"])
                )
            )
            or (
                (float(point.group(2)), float(point.group(1)))
                != (record["latitude"], record["longitude"])
            )
        ):
            raise ValueError(f"chain-city point changed: {opis_id}")


def validate_exact_city(record: dict, context: CatalogValidationContext) -> None:
    opis_id, state = record["opis_id"], record["state"]
    if record["rule"] in {"unique_name_city_road", "unique_distinctive_name_city"}:
        places = context.long_branch_places[state].values()
        matches = [
            (place, address)
            for place in places
            if normalized(place["name"] or "") == normalized(record["csv_name"])
            for address in place["addresses"] or []
            if address.get("region") == state
            and address.get("country") == "US"
            and (
                (address.get("locality") or "").casefold()
                == record["csv_city"].casefold()
            )
        ]
        if {place["id"] for place, _ in matches} != {record["overture_id"]}:
            raise ValueError(f"exact name and city changed: {opis_id}")
        place, address = matches[0]
        point = re.fullmatch(
            "POINT \\((-?\\d+(?:\\.\\d+)?) (-?\\d+(?:\\.\\d+)?)\\)", place["point_wkt"]
        )
        csv_roads = set().union(
            *(road_numbers(value) for value in context.source[opis_id]["addresses"])
        )
        overlap = sorted(csv_roads & road_numbers(address["freeform"] or ""))
        occurrences = sum(
            normalized(other["name"] or "") == normalized(record["csv_name"])
            for other in places
        )
        if (
            point is None
            or (float(point.group(2)), float(point.group(1)))
            != (record["latitude"], record["longitude"])
            or place["name"] != record["overture_name"]
            or (address["freeform"] != record["overture_address"])
            or (place["basic_category"] not in ALLOWED_CATEGORIES)
            or ((place["confidence"] or 0) < 0.75)
            or (place["operating_status"] not in {None, "open"})
            or (record["name_occurrences_state"] != occurrences)
            or (record["road_number_overlap"] != overlap)
            or (record["rule"] == "unique_name_city_road" and (not overlap))
            or (
                record["rule"] == "unique_distinctive_name_city"
                and (occurrences > 3 or len(tokens(record["csv_name"])) < 2)
            )
        ):
            raise ValueError(f"relaxed exact-name evidence differs: {opis_id}")
