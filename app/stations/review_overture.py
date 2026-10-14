import hashlib
import json

from app.stations.review_context import ReviewContext


def load_exact(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    exact_path = root / "var/overture_states/exact_sites.json"
    if exact_path.exists():
        catalog_bytes = exact_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        if (
            catalog["source_sha256"] != source_hash
            or catalog["status"] != "unique_exact_name_city"
            or catalog["verified_count"] != len(catalog["records"])
        ):
            raise ValueError("Overture exact-site catalog is stale")
        for state, expected_hash in catalog["state_catalogs"].items():
            state_bytes = (
                root / f"var/overture_states/{state.lower()}_places.ndjson"
            ).read_bytes()
            manifest = json.loads(
                (
                    root / f"var/overture_states/{state.lower()}_manifest.json"
                ).read_text()
            )
            if (
                manifest["state"] != state
                or manifest["release"] != catalog["release"]
                or manifest["output_sha256"] != expected_hash
                or hashlib.sha256(state_bytes).hexdigest() != expected_hash
            ):
                raise ValueError(f"Overture state evidence changed: {state}")
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            place_id = record["overture_id"]
            if (
                reviews.get(opis_id, {})
                .get("site_id", "")
                .startswith(("ta:", "pilot-family:"))
            ):
                # Prefer the operator's current site workbook when an older
                # Overture place maps the same CSV identity elsewhere.
                continue
            if (
                (
                    opis_id in reviews
                    and not (
                        record["rule"]
                        in {"unique_chain_name_city", "unique_fuzzy_chain_city"}
                        and reviews[opis_id].get("status")
                        in {"review", "operator_store_missing", "operator_id_linked"}
                        and reviews[opis_id].get("overture_id") in {None, place_id}
                    )
                )
                or record["state"] not in catalog["state_catalogs"]
                or record["rule"]
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
                or record["site_id"] != f"overture:{place_id}"
                or record["overture_category"]
                not in {
                    "gas_station",
                    "convenience_store",
                    "truck_stop",
                    "fueling_station",
                }
                or record["overture_confidence"]
                < (
                    0.75
                    if record["rule"]
                    in {
                        "unique_long_branch_city",
                        "unique_name_city_road",
                        "unique_distinctive_name_city",
                    }
                    else 0.85
                    if record["rule"]
                    in {"unique_chain_name_city", "unique_fuzzy_chain_city"}
                    else 0.8
                    if record["rule"]
                    in {
                        "unique_branch_name_city_strong",
                        "unique_state_name_road_near_csv_city",
                        "unique_branch_name_near_csv_city",
                        "unique_csv_name_road_city",
                        "unique_distinctive_name_local_site",
                    }
                    else 0.9
                )
                or (
                    record["rule"] == "unique_exact_name_city_distinctive"
                    and record["name_occurrences_state"] > 2
                    and not record["road_number_overlap"]
                )
                or (
                    record["rule"] == "unique_name_city_road"
                    and not record["road_number_overlap"]
                )
                or (
                    record["rule"] == "unique_csv_name_road_city"
                    and (
                        not record["road_number_overlap"]
                        or record["name_similarity"] < 0.65
                    )
                )
                or (
                    record["rule"] == "unique_distinctive_name_local_site"
                    and record["name_similarity"] < 0.85
                )
                or (
                    record["rule"] == "unique_fuzzy_chain_city"
                    and record["name_similarity"] < 0.75
                )
                or (
                    record["rule"] == "unique_distinctive_name_city"
                    and record["name_occurrences_state"] > 3
                )
                or (
                    record["rule"]
                    in {
                        "unique_branch_name_city",
                        "unique_branch_name_city_strong",
                        "unique_branch_name_near_csv_city",
                    }
                    and record["name_similarity"] < 0.9
                )
                or (
                    record["rule"] == "unique_state_name_road_near_csv_city"
                    and (
                        not record["road_number_overlap"]
                        or record["city_center_distance_miles"] > 10.0
                    )
                )
                or (
                    record["rule"] == "unique_state_name_near_csv_city"
                    and (
                        record["city_center_distance_miles"] > 5.0
                        or record["road_number_overlap"]
                    )
                )
                or (
                    record["rule"] == "unique_branch_name_near_csv_city"
                    and record["city_center_distance_miles"] > 8.0
                )
                or not -90 <= record["latitude"] <= 90
                or not -180 <= record["longitude"] <= 180
            ):
                raise ValueError(f"Overture exact-site link conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": place_id,
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["overture_exact_sites"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "state_catalogs": catalog["state_catalogs"],
            "verified_count": catalog["verified_count"],
        }
