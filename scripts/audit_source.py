import csv
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "data/raw/fuel-prices-for-be-assessment.csv"
raw = source.read_bytes()
with source.open(encoding="utf-8-sig", newline="") as stream:
    reader = csv.DictReader(stream)
    columns = reader.fieldnames
    rows = list(reader)
us_states = set(
    (
        "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
        "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA "
        "WV WI WY DC"
    ).split()
)
by_id = defaultdict(list)
for row in rows:
    by_id[row["OPIS Truckstop ID"]].append(row)
us = [row for row in rows if row["State"].strip().upper() in us_states]
non_us = [row for row in rows if row["State"].strip().upper() not in us_states]
prices = []
bad_prices = []
for i, row in enumerate(rows, 2):
    try:
        price = Decimal(row["Retail Price"])
        if not price.is_finite() or price <= 0:
            raise InvalidOperation
        prices.append(price)
    except InvalidOperation:
        bad_prices.append(i)
fields = ("Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price")
conflicts = {
    field: [
        id_
        for id_, group in by_id.items()
        if len({r[field].strip().upper() for r in group}) > 1
    ]
    for field in fields
}
price_conflicts = [
    {
        "station_id": id_,
        "rows": len(group),
        "prices": sorted({r["Retail Price"] for r in group}),
        "locations": sorted(
            {" | ".join(r[f] for f in ("Address", "City", "State")) for r in group}
        ),
    }
    for id_, group in by_id.items()
    if len({Decimal(r["Retail Price"]) for r in group}) > 1
]
result = {
    "audit_date": "2026-10-07",
    "source_filename": source.name,
    "sha256": hashlib.sha256(raw).hexdigest(),
    "bytes": len(raw),
    "columns": columns,
    "row_count": len(rows),
    "unique_station_ids": len(by_id),
    "duplicate_id_groups": sum(len(group) > 1 for group in by_id.values()),
    "exact_duplicate_excess_rows": len(rows)
    - len({tuple(row[c] for c in columns) for row in rows}),
    "us_row_count": len(us),
    "us_unique_station_ids": len({r["OPIS Truckstop ID"] for r in us}),
    "non_us_row_count": len(non_us),
    "non_us_unique_station_ids": len({r["OPIS Truckstop ID"] for r in non_us}),
    "state_row_counts": dict(sorted(Counter(r["State"] for r in rows).items())),
    "us_state_codes_missing": sorted(us_states - {r["State"] for r in us}),
    "blank_fields": {c: sum(not r[c].strip() for r in rows) for c in columns},
    "invalid_price_source_lines": bad_prices,
    "minimum_price": str(min(prices)),
    "maximum_price": str(max(prices)),
    "maximum_price_decimal_places": max(-p.as_tuple().exponent for p in prices),
    "duplicate_id_conflict_counts": {f: len(ids) for f, ids in conflicts.items()},
    "us_duplicate_id_price_conflicts": sum(
        len({Decimal(r["Retail Price"]) for r in group}) > 1
        and group[0]["State"] in us_states
        for group in by_id.values()
    ),
    "duplicate_id_conflict_sample_ids": {f: ids[:10] for f, ids in conflicts.items()},
    "price_conflict_examples": price_conflicts[:5],
    "largest_id_groups": [
        {"station_id": id_, "rows": len(group)}
        for id_, group in sorted(by_id.items(), key=lambda x: (-len(x[1]), x[0]))[:10]
    ],
    "geocoded": False,
    "notes": [
        "US filtering uses 50 state abbreviations plus DC.",
        "Conflict counts compare trimmed uppercase text; price conflict examples "
        "compare Decimal values.",
        "No fuel type, currency, unit, observation timestamp, latitude, or "
        "longitude columns exist.",
    ],
}
(root / "data/audit/source-profile.json").write_text(
    json.dumps(result, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(result, indent=2))
