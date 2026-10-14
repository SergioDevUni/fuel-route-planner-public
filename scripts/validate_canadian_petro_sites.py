"""Replay two reviewed Petro-Canada highway-site links against public points."""

import json

from app.stations.catalogs.canadian_petro_sites import CATALOG as CATALOG
from app.stations.catalogs.canadian_petro_sites import EVIDENCE as EVIDENCE
from app.stations.catalogs.canadian_petro_sites import EXPECTED as EXPECTED
from app.stations.catalogs.canadian_petro_sites import validate as validate
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
