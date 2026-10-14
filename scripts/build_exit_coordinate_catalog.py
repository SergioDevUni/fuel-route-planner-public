"""Replace CSV locality representatives with matching interstate exit points.

An exit point locates the highway interchange named by the CSV. It does not
identify a pump, driveway, or a particular business among stores at the exit.
"""

import json

from app.stations.catalogs.exit_coordinate_catalog import (
    CSV_COORDINATES as CSV_COORDINATES,
)
from app.stations.catalogs.exit_coordinate_catalog import (
    MAX_CITY_DISTANCE_MILES as MAX_CITY_DISTANCE_MILES,
)
from app.stations.catalogs.exit_coordinate_catalog import OUTPUT as OUTPUT
from app.stations.catalogs.exit_coordinate_catalog import RELEASE as RELEASE
from app.stations.catalogs.exit_coordinate_catalog import STATES as STATES
from app.stations.catalogs.exit_coordinate_catalog import _exit_index as _exit_index
from app.stations.catalogs.exit_coordinate_catalog import build_catalog as build_catalog
from app.stations.catalogs.exit_coordinate_catalog import (
    distance_miles as distance_miles,
)
from app.stations.catalogs.exit_coordinate_catalog import (
    road_and_exits as road_and_exits,
)
from app.stations.catalogs.exit_coordinate_catalog import sha256 as sha256
from app.stations.catalogs.exit_coordinate_catalog import (
    validate_catalog as validate_catalog,
)
from app.stations.catalogs.paths import ROOT as ROOT


def main() -> None:
    catalog = build_catalog()
    path = ROOT / OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    print(f"{len(catalog['records'])} interstate exit points built")


if __name__ == "__main__":
    main()
