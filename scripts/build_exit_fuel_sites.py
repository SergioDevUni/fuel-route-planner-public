"""Promote reviewed, distinctive exit-linked OSM fuel places to site points."""

import json

from app.stations.catalogs.exit_fuel_sites import OUTPUT as OUTPUT
from app.stations.catalogs.exit_fuel_sites import REVIEWED_NAMES as REVIEWED_NAMES
from app.stations.catalogs.exit_fuel_sites import (
    REVIEWED_SOURCE_SITES as REVIEWED_SOURCE_SITES,
)
from app.stations.catalogs.exit_fuel_sites import _rows as _rows
from app.stations.catalogs.exit_fuel_sites import build_catalog as build_catalog
from app.stations.catalogs.exit_fuel_sites import validate_catalog as validate_catalog
from app.stations.catalogs.paths import ROOT


def main() -> None:
    catalog = build_catalog()
    path = ROOT / OUTPUT
    path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    print(f"{len(catalog['records'])} distinctive fuel site points built")


if __name__ == "__main__":
    main()
