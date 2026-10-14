"""Validate two CSV Gas N Wash sites against Illinois and Overture records."""

from app.stations.catalogs.illinois_gasnwash_sites import CATALOG as CATALOG
from app.stations.catalogs.illinois_gasnwash_sites import MANIFEST as MANIFEST
from app.stations.catalogs.illinois_gasnwash_sites import REVIEW as REVIEW
from app.stations.catalogs.illinois_gasnwash_sites import compact as compact
from app.stations.catalogs.illinois_gasnwash_sites import validate as validate
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

if __name__ == "__main__":
    print(f"{len(validate()['records'])} Illinois Gas N Wash CSV sites")
