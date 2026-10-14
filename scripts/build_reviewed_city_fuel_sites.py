"""Promote individually corroborated CSV locality links to mapped fuel sites."""

import json

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.reviewed_city_fuel_sites import (
    CSV_COORDINATES as CSV_COORDINATES,
)
from app.stations.catalogs.reviewed_city_fuel_sites import EXIT_CATALOG as EXIT_CATALOG
from app.stations.catalogs.reviewed_city_fuel_sites import OUTPUT as OUTPUT
from app.stations.catalogs.reviewed_city_fuel_sites import REVIEWS as REVIEWS
from app.stations.catalogs.reviewed_city_fuel_sites import (
    build_catalog as build_catalog,
)
from app.stations.catalogs.reviewed_city_fuel_sites import normalized as normalized
from app.stations.catalogs.reviewed_city_fuel_sites import sha256 as sha256
from app.stations.catalogs.reviewed_city_fuel_sites import (
    validate_catalog as validate_catalog,
)


def main() -> None:
    catalog = build_catalog()
    path = ROOT / OUTPUT
    path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    print(f"{len(catalog['records'])} reviewed city fuel sites built")


if __name__ == "__main__":
    main()
