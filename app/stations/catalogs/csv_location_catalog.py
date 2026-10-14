"""Shared csv location catalog rules used by review and preparation tools."""

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from app.stations.catalogs.regions import STATES


def build_catalog(source: Path, profile: Path) -> dict:
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    audit = json.loads(profile.read_text())
    if source_hash != audit["sha256"]:
        raise ValueError("CSV source differs from audited input")

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            opis_id = row["OPIS Truckstop ID"].strip()
            if not opis_id.isdecimal():
                raise ValueError(f"invalid OPIS ID {opis_id!r}")
            groups[opis_id].append(row)
    if len(groups) != audit["unique_station_ids"]:
        raise ValueError("CSV location catalog ID count differs from audit")

    records = []
    for opis_id, rows in sorted(groups.items(), key=lambda pair: int(pair[0])):
        names = sorted({row["Truckstop Name"].strip() for row in rows})
        addresses = {row["Address"].strip() for row in rows}
        cities = {row["City"].strip() for row in rows}
        states = {row["State"].strip().upper() for row in rows}
        if (
            not all(names)
            or len(addresses) != 1
            or len(cities) != 1
            or len(states) != 1
            or not next(iter(addresses))
            or not next(iter(cities))
            or not next(iter(states))
        ):
            raise ValueError(f"missing or conflicting CSV location for {opis_id}")
        state = next(iter(states))
        records.append(
            {
                "opis_id": opis_id,
                "names": names,
                "address": next(iter(addresses)),
                "city": next(iter(cities)),
                "state": state,
                "country": "US" if state in STATES else "CA",
                "location_source": "supplied_csv",
                "location_link_status": "csv_address_linked",
            }
        )
    return {
        "schema_version": 1,
        "source_sha256": source_hash,
        "total_csv_station_ids": len(records),
        "csv_address_linked_station_ids": len(records),
        "records": records,
    }
