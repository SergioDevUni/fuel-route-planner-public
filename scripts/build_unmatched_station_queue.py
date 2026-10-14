"""Build an internal, source-hashed queue of CSV IDs still lacking a site link."""

import json

from app.stations.catalogs.paths import ROOT
from app.stations.catalogs.source_queue import ACCOUNTING as ACCOUNTING
from app.stations.catalogs.source_queue import (
    ACCOUNTING_MANIFEST as ACCOUNTING_MANIFEST,
)
from app.stations.catalogs.source_queue import CHAINS as CHAINS
from app.stations.catalogs.source_queue import PROFILE as PROFILE
from app.stations.catalogs.source_queue import SNAPSHOT_MANIFEST as SNAPSHOT_MANIFEST
from app.stations.catalogs.source_queue import build_queue as build_queue
from app.stations.catalogs.source_queue import family as family

OUTPUT = ROOT / "var/station_locations/unmatched_queue.json"


def main() -> None:
    result = build_queue()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"{result['unmatched_station_ids']} unmatched of "
        f"{result['total_csv_station_ids']} CSV IDs"
    )
    print(json.dumps(result["by_family"], sort_keys=True))


if __name__ == "__main__":
    main()
