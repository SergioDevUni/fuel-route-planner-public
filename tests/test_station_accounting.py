import pytest

from app.stations.accounting import build_location_accounting


def test_verified_site_stays_unresolved_without_access_evidence() -> None:
    stations = [
        {"opis_id": "1", "state": "MI", "location_status": "unresolved"},
        {"opis_id": "2", "state": "MI", "location_status": "unresolved"},
        {"opis_id": "3", "state": "CA", "location_status": "quarantined"},
    ]
    reviews = {
        "1": {"status": "site_verified", "overture_id": "feature-1"},
        "2": {"status": "review", "overture_id": None},
    }
    rows, summary = build_location_accounting(stations, reviews, {"1"})
    assert rows[0]["status"] == "unresolved"
    assert rows[0]["reason"] == "vehicle_access_pending_check"
    assert rows[0]["provider_feature_id"] == "feature-1"
    assert rows[1]["status"] == "ambiguous"
    assert rows[2]["reason"] == "conflicting_source_locations"
    assert summary["route_ready_count"] == 0
    assert summary["total_station_ids"] == 3
    assert summary["status_counts"] == {"ambiguous": 2, "unresolved": 1}


def test_review_and_validated_identity_must_agree() -> None:
    stations = [{"opis_id": "1", "state": "IL", "location_status": "unresolved"}]
    with pytest.raises(ValueError, match="disagrees"):
        build_location_accounting(
            stations, {"1": {"status": "site_verified", "overture_id": "poi"}}, set()
        )


def test_operator_id_link_is_not_a_verified_coordinate() -> None:
    stations = [
        {"opis_id": "1", "state": "IL", "location_status": "unresolved"},
        {"opis_id": "2", "state": "UT", "location_status": "unresolved"},
    ]
    reviews = {
        "1": {"status": "operator_id_linked", "overture_id": None},
        "2": {"status": "operator_id_conflict", "overture_id": None},
    }
    rows, summary = build_location_accounting(stations, reviews, set())
    assert rows[0]["identity_status"] == "pending_check"
    assert rows[0]["reason"] == "operator_id_linked_coordinate_pending_check"
    assert rows[1]["status"] == "ambiguous"
    assert rows[1]["reason"] == "conflicting_operator_opis"
    assert summary["route_ready_count"] == 0


def test_conflicted_price_keeps_both_physical_sites_out_of_routes() -> None:
    stations = [
        {"opis_id": "1914", "state": "UT", "location_status": "unresolved"},
        {"opis_id": "70793", "state": "UT", "location_status": "unresolved"},
    ]
    sites = ["loves:ut:green-river:119", "loves:ut:green-river:792"]
    reviews = {
        item["opis_id"]: {
            "status": "operator_id_conflict",
            "candidate_site_ids": sites,
            "overture_id": None,
        }
        for item in stations
    }
    rows, summary = build_location_accounting(stations, reviews, set())
    assert all(row["reason"] == "historical_price_site_conflict" for row in rows)
    assert all(row["candidate_site_ids"] == sites for row in rows)
    assert all(row["identity_status"] == "pending_check" for row in rows)
    assert summary["status_counts"] == {"ambiguous": 2}
    assert summary["route_ready_count"] == 0


def test_csv_store_identity_is_verified_but_access_still_unchecked() -> None:
    stations = [
        {"opis_id": "1914", "state": "UT", "location_status": "unresolved"},
        {"opis_id": "70793", "state": "UT", "location_status": "unresolved"},
    ]
    reviews = {
        "1914": {
            "status": "csv_store_site_linked",
            "site_id": "loves:ut:green-river:792",
        },
        "70793": {
            "status": "csv_store_site_linked",
            "site_id": "loves:ut:green-river:119",
        },
    }
    rows, summary = build_location_accounting(stations, reviews, set(reviews))
    assert [row["matched_site_id"] for row in rows] == [
        "loves:ut:green-river:792",
        "loves:ut:green-river:119",
    ]
    assert all(row["identity_status"] == "verified" for row in rows)
    assert all(row["reason"] == "road_access_unchecked" for row in rows)
    assert summary["route_ready_count"] == 0


def test_csv_locality_point_is_linked_without_claiming_site_verification() -> None:
    stations = [{"opis_id": "1", "state": "IL", "location_status": "unresolved"}]
    reviews = {"1": {"status": "csv_location_coordinate", "site_id": "geonames:123"}}
    rows, summary = build_location_accounting(stations, reviews, {"1"})
    assert rows[0]["identity_status"] == "location_estimated"
    assert rows[0]["provider"] == "geonames"
    assert rows[0]["reason"] == "csv_locality_point_not_site_verified"
    assert summary["route_ready_count"] == 0


def test_csv_exit_point_is_linked_without_claiming_site_verification() -> None:
    stations = [{"opis_id": "1", "state": "ND", "location_status": "unresolved"}]
    reviews = {
        "1": {"status": "csv_exit_coordinate", "site_id": "openinterstate:node/123"}
    }
    rows, _ = build_location_accounting(stations, reviews, {"1"})
    assert rows[0]["identity_status"] == "location_estimated"
    assert rows[0]["provider"] == "openinterstate"
    assert rows[0]["reason"] == "csv_exit_point_not_site_verified"
