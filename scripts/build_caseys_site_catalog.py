"""Link numbered CSV Casey's stores to permitted Overture site points.

Official store URLs were found through browser search. The URL supplies the
operator's branch number and street; Overture supplies the stored coordinate.
"""

import json
import os

from app.stations.catalogs.caseys_site_catalog import ADDRESS_STOP as ADDRESS_STOP
from app.stations.catalogs.caseys_site_catalog import CITY_PLACES as CITY_PLACES
from app.stations.catalogs.caseys_site_catalog import HOUSE as HOUSE
from app.stations.catalogs.caseys_site_catalog import OVERTURE as OVERTURE
from app.stations.catalogs.caseys_site_catalog import URLS as URLS
from app.stations.catalogs.caseys_site_catalog import build_catalog as build_catalog
from app.stations.catalogs.caseys_site_catalog import choose_site as choose_site
from app.stations.catalogs.caseys_site_catalog import official_rows as official_rows
from app.stations.catalogs.caseys_site_catalog import parsed_url as parsed_url
from app.stations.catalogs.caseys_site_catalog import road_match as road_match
from app.stations.catalogs.caseys_site_catalog import (
    validate_catalog as validate_catalog,
)
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

OUTPUT = ROOT / "var/operator_review/caseys_site_catalog.json"


def main() -> None:
    document = build_catalog()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_name(OUTPUT.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, OUTPUT)
    count = document["verified_count"]
    urls = document["official_url_count"]
    print(f"Casey's: {count}/{urls} official URLs linked")


if __name__ == "__main__":
    main()
