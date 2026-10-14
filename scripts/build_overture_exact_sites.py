"""Build the pinned Overture catalog using reusable station matching."""

from app.stations.catalogs.overture_catalog import build

if __name__ == "__main__":
    print(f"{build()['verified_count']} exact Overture CSV links")
