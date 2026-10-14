"""Give CSV locations without a mapped fuel site a labelled locality point.

These GeoNames points represent localities. The CSV supplies station identity,
address, and price.
"""

import json

from app.stations.catalogs.csv_coordinate_catalog import CA_ARCHIVE as CA_ARCHIVE
from app.stations.catalogs.csv_coordinate_catalog import (
    CANADIAN_PROVINCES as CANADIAN_PROVINCES,
)
from app.stations.catalogs.csv_coordinate_catalog import CSV_LOCATIONS as CSV_LOCATIONS
from app.stations.catalogs.csv_coordinate_catalog import FALLBACK_IDS as FALLBACK_IDS
from app.stations.catalogs.csv_coordinate_catalog import (
    OTHER_LOCALITIES as OTHER_LOCALITIES,
)
from app.stations.catalogs.csv_coordinate_catalog import OUTPUT as OUTPUT
from app.stations.catalogs.csv_coordinate_catalog import (
    ROAD_RESOLVED_PLACES as ROAD_RESOLVED_PLACES,
)
from app.stations.catalogs.csv_coordinate_catalog import US_PLACES as US_PLACES
from app.stations.catalogs.csv_coordinate_catalog import _places as _places
from app.stations.catalogs.csv_coordinate_catalog import build_catalog as build_catalog
from app.stations.catalogs.csv_coordinate_catalog import compact as compact
from app.stations.catalogs.csv_coordinate_catalog import sha256 as sha256
from app.stations.catalogs.csv_coordinate_catalog import (
    validate_catalog as validate_catalog,
)
from app.stations.catalogs.paths import ROOT as ROOT

# Multiple places can have the same name in one state. The CSV road clues
# distinguish these seven otherwise tied locality records.


def main() -> None:
    path = ROOT / OUTPUT
    result = build_catalog()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"{len(result['records'])} CSV location points built")


if __name__ == "__main__":
    main()
