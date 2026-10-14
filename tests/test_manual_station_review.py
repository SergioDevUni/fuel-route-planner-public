"""Manual site approval stays bound to source identity and coordinates."""

import json

import pytest

from app.stations.manual_review import REVIEW_PATH, apply_manual_review


def reviewed(root):
    rows = [
        {
            "station_id": station_id,
            "latitude": 41.0,
            "longitude": -88.0,
            "price_per_gallon": price,
            "location_precision": "city_representative",
        }
        for station_id, price in (("1", "3.10"), ("2", "3.40"))
    ]
    review = {
        "schema": 1,
        "source_sha256": "source",
        "basis": "project_owner_confirmation",
        "confirmed_date": "2026-10-12",
        "stations": [
            {
                "station_id": row["station_id"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "source_location_precision": row["location_precision"],
            }
            for row in rows
        ],
    }
    path = root / REVIEW_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(review))
    return rows, review, path


def test_manual_review_preserves_distinct_offers_and_coordinates(tmp_path):
    rows, _, _ = reviewed(tmp_path)
    before = [
        (r["station_id"], r["price_per_gallon"], r["latitude"], r["longitude"])
        for r in rows
    ]
    receipt = apply_manual_review(tmp_path, "source", rows)
    assert receipt["station_count"] == 2
    assert before == [
        (r["station_id"], r["price_per_gallon"], r["latitude"], r["longitude"])
        for r in rows
    ]
    assert all(r["location_precision"] == "fuel_station_site" for r in rows)
    assert all(r["source_location_precision"] == "city_representative" for r in rows)


@pytest.mark.parametrize("change", ["coordinate", "missing", "duplicate", "source"])
def test_manual_review_cannot_approve_changed_or_unreviewed_data(tmp_path, change):
    rows, review, path = reviewed(tmp_path)
    if change == "coordinate":
        rows[0]["latitude"] += 0.1
    elif change == "missing":
        review["stations"].pop()
    elif change == "duplicate":
        review["stations"].append(review["stations"][0])
    else:
        review["source_sha256"] = "another-source"
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError):
        apply_manual_review(tmp_path, "source", rows)
