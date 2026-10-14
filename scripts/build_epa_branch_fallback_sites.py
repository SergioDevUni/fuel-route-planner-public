"""Resolve remaining CSV branches against unique nearby open UST facilities."""

import json

from app.stations.catalogs.epa_branch_fallback_sites import (
    CSV_COORDINATES as CSV_COORDINATES,
)
from app.stations.catalogs.epa_branch_fallback_sites import GENERIC as GENERIC
from app.stations.catalogs.epa_branch_fallback_sites import (
    MAX_CITY_DISTANCE_MILES as MAX_CITY_DISTANCE_MILES,
)
from app.stations.catalogs.epa_branch_fallback_sites import OUTPUT as OUTPUT
from app.stations.catalogs.epa_branch_fallback_sites import PREVIOUS as PREVIOUS
from app.stations.catalogs.epa_branch_fallback_sites import branch as branch
from app.stations.catalogs.epa_branch_fallback_sites import (
    build_catalog as build_catalog,
)
from app.stations.catalogs.epa_branch_fallback_sites import (
    credible_pair as credible_pair,
)
from app.stations.catalogs.epa_branch_fallback_sites import (
    identity_words as identity_words,
)
from app.stations.catalogs.epa_branch_fallback_sites import numbers as numbers
from app.stations.catalogs.epa_branch_fallback_sites import sha256 as sha256
from app.stations.catalogs.epa_branch_fallback_sites import (
    validate_catalog as validate_catalog,
)
from app.stations.catalogs.paths import ROOT as ROOT


def main() -> None:
    catalog = build_catalog()
    path = ROOT / OUTPUT
    path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    print(f"{len(catalog['records'])} EPA branch fallback sites built")


if __name__ == "__main__":
    main()
