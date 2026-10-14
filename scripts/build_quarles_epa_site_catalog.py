"""Link Quarles guide branches to source-hashed public Q-Card UST points."""

import json
import os

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE
from app.stations.catalogs.quarles_epa_site_catalog import OPERATOR as OPERATOR
from app.stations.catalogs.quarles_epa_site_catalog import OUTPUT as OUTPUT
from app.stations.catalogs.quarles_epa_site_catalog import (
    branch_suffix_matches as branch_suffix_matches,
)
from app.stations.catalogs.quarles_epa_site_catalog import build as build
from app.stations.catalogs.quarles_epa_site_catalog import (
    eligible_stations as eligible_stations,
)
from app.stations.catalogs.quarles_epa_site_catalog import (
    matching_facilities as matching_facilities,
)
from app.stations.catalogs.quarles_epa_site_catalog import (
    validate_catalog as validate_catalog,
)

if __name__ == "__main__":
    catalog = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, OUTPUT)
    print(f"Quarles public Q-Card site links: {catalog['verified_count']}")
