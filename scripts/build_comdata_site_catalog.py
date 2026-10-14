"""Build the public-address Comdata station catalog."""

from app.stations.catalogs.comdata_catalog import build

if __name__ == "__main__":
    print(f"{build()['verified_count']} public-address Overture links")
