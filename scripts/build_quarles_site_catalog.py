"""Link Quarles CSV branches through its public address guide and Overture."""

import json

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE
from app.stations.catalogs.quarles_site_catalog import BRANCH as BRANCH
from app.stations.catalogs.quarles_site_catalog import GUIDE as GUIDE
from app.stations.catalogs.quarles_site_catalog import OUTPUT as OUTPUT
from app.stations.catalogs.quarles_site_catalog import OVERTURE as OVERTURE
from app.stations.catalogs.quarles_site_catalog import (
    _candidate_stations as _candidate_stations,
)
from app.stations.catalogs.quarles_site_catalog import build as build
from app.stations.catalogs.quarles_site_catalog import choose_place as choose_place
from app.stations.catalogs.quarles_site_catalog import close_points as close_points
from app.stations.catalogs.quarles_site_catalog import guide_address as guide_address
from app.stations.catalogs.quarles_site_catalog import guide_rows as guide_rows
from app.stations.catalogs.quarles_site_catalog import house_number as house_number
from app.stations.catalogs.quarles_site_catalog import street_tokens as street_tokens
from app.stations.catalogs.quarles_site_catalog import (
    validate_catalog as validate_catalog,
)

if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    catalog = build()
    OUTPUT.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"Quarles verified sites: {catalog['verified_count']}")
