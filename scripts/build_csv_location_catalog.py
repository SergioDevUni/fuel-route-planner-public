"""Preserve every OPIS ID's authoritative location description from the CSV.

This catalog links an ID to the supplied station address. It does not invent a
map point or make an address-only record eligible for route optimization.
"""

import json
import os
from pathlib import Path

from app.stations.catalogs.csv_location_catalog import build_catalog as build_catalog

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/fuel-prices-for-be-assessment.csv"
PROFILE = ROOT / "data/audit/source-profile.json"
OUTPUT = ROOT / "var/station_locations/csv_location_catalog.json"


def main() -> None:
    catalog = build_catalog(SOURCE, PROFILE)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, OUTPUT)
    print(f"CSV address links: {catalog['csv_address_linked_station_ids']}")


if __name__ == "__main__":
    main()
