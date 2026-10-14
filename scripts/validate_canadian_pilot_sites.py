"""Replay reviewed Canadian Pilot site links against CSV and pinned Overture points."""

import json

from app.stations.catalogs.canadian_pilot_sites import CATALOG as CATALOG
from app.stations.catalogs.canadian_pilot_sites import EVIDENCE as EVIDENCE
from app.stations.catalogs.canadian_pilot_sites import (
    EXPECTED_BRANCHES as EXPECTED_BRANCHES,
)
from app.stations.catalogs.canadian_pilot_sites import (
    distance_meters as distance_meters,
)
from app.stations.catalogs.canadian_pilot_sites import validate as validate
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
