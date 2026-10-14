"""Pure station-location status accounting; never promotes a candidate to usable."""

from collections import Counter, defaultdict

REQUIRED_CSV_SITE_COVERAGE_PERCENT = 100.0

SITE_IDENTIFIED = frozenset(
    {"site_verified", "operator_coordinate", "csv_store_site_linked"}
)
LOCATION_IDENTIFIED = SITE_IDENTIFIED | {
    "csv_location_coordinate",
    "csv_exit_coordinate",
}
REVIEW_STATES = LOCATION_IDENTIFIED | {
    "review",
    "source_missing",
    "operator_id_linked",
    "operator_id_conflict",
    "operator_store_missing",
}


def build_location_accounting(
    stations: list[dict], reviews: dict[str, dict], verified_ids: set[str]
) -> tuple[list[dict], dict]:
    station_ids = {item["opis_id"] for item in stations}
    if len(station_ids) != len(stations):
        raise ValueError("duplicate source station IDs")
    if unknown := set(reviews) - station_ids:
        raise ValueError(f"review IDs missing from source dataset: {sorted(unknown)}")
    if verified_ids != {
        opis_id
        for opis_id, review in reviews.items()
        if review["status"] in LOCATION_IDENTIFIED
    }:
        raise ValueError("site-verified output disagrees with review decisions")

    rows = []
    by_state: dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    review_totals: Counter[str] = Counter()
    for station in sorted(stations, key=lambda item: int(item["opis_id"])):
        opis_id = station["opis_id"]
        if station["location_status"] not in {"unresolved", "quarantined"}:
            raise ValueError(f"unsupported source location status for {opis_id}")
        review = reviews.get(opis_id)
        review_status = review["status"] if review else "unreviewed"
        if review_status not in REVIEW_STATES | {"unreviewed"}:
            raise ValueError(f"invalid review status for {opis_id}")
        identity_status = (
            "verified"
            if review_status in SITE_IDENTIFIED
            else "location_estimated"
            if review_status in {"csv_location_coordinate", "csv_exit_coordinate"}
            else "pending_check"
        )
        if station["location_status"] == "quarantined":
            status = "ambiguous"
            reason = "conflicting_source_locations"
        elif review_status == "operator_id_conflict":
            status = "ambiguous"
            reason = (
                "historical_price_site_conflict"
                if review and review.get("candidate_site_ids")
                else "conflicting_operator_opis"
            )
        elif review_status == "review":
            status = "ambiguous"
            reason = "candidate_identity_unresolved"
        elif identity_status == "verified":
            status = "unresolved"
            reason = (
                "road_access_unchecked"
                if review_status == "csv_store_site_linked"
                else "vehicle_access_pending_check"
            )
        elif identity_status == "location_estimated":
            status = "unresolved"
            reason = (
                "csv_exit_point_not_site_verified"
                if review_status == "csv_exit_coordinate"
                else "csv_locality_point_not_site_verified"
            )
        elif review_status == "source_missing":
            status = "unresolved"
            reason = "map_source_missing"
        elif review_status == "operator_id_linked":
            status = "unresolved"
            reason = "operator_id_linked_coordinate_pending_check"
        elif review_status == "operator_store_missing":
            status = "unresolved"
            reason = "operator_store_missing"
        else:
            status = "unresolved"
            reason = "identity_unreviewed"
        row = {
            "opis_id": opis_id,
            "state": station["state"],
            "status": status,
            "reason": reason,
            "review_status": review_status,
            "identity_status": identity_status,
            "access_status": "pending_check",
            "provider": (
                "openinterstate"
                if review_status == "csv_exit_coordinate"
                else "geonames"
                if review_status == "csv_location_coordinate"
                else "epa_ust"
                if review and str(review.get("site_id", "")).startswith("epa-ust:")
                else "operator"
                if review_status in {"operator_coordinate", "csv_store_site_linked"}
                else "overture"
            )
            if identity_status != "pending_check"
            else None,
            "provider_feature_id": (
                review["overture_id"] if review_status == "site_verified" else None
            )
            if review
            else None,
            "candidate_site_ids": review.get("candidate_site_ids", [])
            if review
            else [],
            "matched_site_id": review.get("site_id") if review else None,
        }
        rows.append(row)
        totals[status] += 1
        by_state[station["state"]][status] += 1
        review_totals[review_status] += 1
    summary = {
        "total_station_ids": len(rows),
        "status_counts": dict(sorted(totals.items())),
        "review_counts": dict(sorted(review_totals.items())),
        "by_state": {
            state: dict(sorted(counts.items()))
            for state, counts in sorted(by_state.items())
        },
        "route_ready_count": 0,
    }
    return rows, summary
