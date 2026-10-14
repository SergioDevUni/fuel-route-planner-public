"""Replay reviewed Ohio 7-Eleven CSV-to-site links from public sources."""

from app.stations.catalogs.ohio_7eleven_sites import EVIDENCE as EVIDENCE
from app.stations.catalogs.ohio_7eleven_sites import OH_MANIFEST as OH_MANIFEST
from app.stations.catalogs.ohio_7eleven_sites import OH_PLACES as OH_PLACES
from app.stations.catalogs.ohio_7eleven_sites import TANK_PDF as TANK_PDF
from app.stations.catalogs.ohio_7eleven_sites import TURNPIKE_PDF as TURNPIKE_PDF
from app.stations.catalogs.ohio_7eleven_sites import validate as validate
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

if __name__ == "__main__":
    print(f"{len(validate()['records'])} Ohio 7-Eleven site links validated")
