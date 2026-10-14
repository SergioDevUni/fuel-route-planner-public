import csv
import hashlib
import json

import pytest

from scripts.build_csv_location_catalog import build_catalog


def write_fixture(tmp_path, rows):
    source = tmp_path / "stations.csv"
    with source.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "OPIS Truckstop ID",
                "Truckstop Name",
                "Address",
                "City",
                "State",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "unique_station_ids": len({row["OPIS Truckstop ID"] for row in rows}),
            }
        )
    )
    return source, profile


def test_csv_address_links_are_unique_per_opis_id(tmp_path) -> None:
    rows = [
        {
            "OPIS Truckstop ID": "341",
            "Truckstop Name": "TA JACKSONVILLE SOUTH TRAVEL CENTER",
            "Address": "I-95, EXIT 329",
            "City": "Saint Johns",
            "State": "FL",
        },
        {
            "OPIS Truckstop ID": "341",
            "Truckstop Name": "TA Jacksonville South",
            "Address": "I-95, EXIT 329",
            "City": "Saint Johns",
            "State": "FL",
        },
    ]
    catalog = build_catalog(*write_fixture(tmp_path, rows))
    assert catalog["csv_address_linked_station_ids"] == 1
    assert catalog["records"][0]["address"] == "I-95, EXIT 329"
    assert len(catalog["records"][0]["names"]) == 2


def test_conflicting_csv_address_fails(tmp_path) -> None:
    rows = [
        {
            "OPIS Truckstop ID": "341",
            "Truckstop Name": "TA Jacksonville South",
            "Address": address,
            "City": "Saint Johns",
            "State": "FL",
        }
        for address in ["I-95, EXIT 329", "I-95, EXIT 330"]
    ]
    with pytest.raises(ValueError, match="conflicting CSV location"):
        build_catalog(*write_fixture(tmp_path, rows))
