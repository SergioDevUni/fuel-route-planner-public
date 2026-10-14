"""Validated, idempotent CSV import with atomic active-version publication."""

import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction

from app.domain.text import canonical_text
from app.stations.models import DatasetVersion, SourcePriceRow, Station

HEADER = (
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
)
US_STATES = frozenset(
    (
        "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
        "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA "
        "WV WI WY DC"
    ).split()
)
PRICE_POLICY = "max_observed_v1"
IMPORT_POLICY = f"{PRICE_POLICY}_all_csv_ids"
CANADIAN_REGIONS = frozenset("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())


class ImportRejected(ValueError):
    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(issues[:5]))


@dataclass(frozen=True, slots=True)
class ParsedRow:
    number: int
    raw: dict[str, str]
    display: dict[str, str]
    price: Decimal | None
    classification: str


def _parse_price(value: str) -> Decimal | None:
    try:
        price = Decimal(value)
    except InvalidOperation:
        return None
    if not price.is_finite() or price <= 0 or -price.as_tuple().exponent > 8:
        return None
    return price


def _read_rows(source: Path) -> tuple[list[ParsedRow], tuple[str, ...]]:
    parsed: list[ParsedRow] = []
    issues: list[str] = []
    exact_rows: set[tuple[str, ...]] = set()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != HEADER:
            raise ImportRejected(
                ("CSV header differs from the expected seven columns",)
            )
        for number, raw in enumerate(reader, start=2):
            if None in raw or any(value is None for value in raw.values()):
                issues.append(f"record {number}: malformed column count")
                continue
            display = {key: value.strip() for key, value in raw.items()}
            price = _parse_price(display["Retail Price"])
            if not display["OPIS Truckstop ID"]:
                issues.append(f"record {number}: empty OPIS ID")
            if price is None:
                issues.append(f"record {number}: invalid price")
            if any(not display[key] for key in HEADER):
                issues.append(f"record {number}: blank required field")
            state = display["State"].upper()
            if state not in US_STATES | CANADIAN_REGIONS:
                issues.append(f"record {number}: unsupported state or province {state}")
            exact_key = tuple(raw[key] for key in HEADER)
            duplicate = exact_key in exact_rows
            exact_rows.add(exact_key)
            region = "us" if display["State"].upper() in US_STATES else "non_us"
            classification = f"duplicate_{region}" if duplicate else region
            parsed.append(ParsedRow(number, raw, display, price, classification))
    return parsed, tuple(issues)


def _station_from_rows(dataset: DatasetVersion, rows: list[ParsedRow]) -> Station:
    first = rows[0]
    location_keys = ("Address", "City", "State")
    locations = {
        tuple(canonical_text(row.display[key]) for key in location_keys) for row in rows
    }
    prices = [row.price for row in rows]
    assert all(price is not None for price in prices)
    minimum = min(prices)
    maximum = max(prices)
    aliases = list(dict.fromkeys(row.display["Truckstop Name"] for row in rows))
    selected_rows = [row for row in rows if row.price == maximum]
    minimum_row = next(row for row in rows if row.price == minimum)
    return Station(
        dataset=dataset,
        opis_id=first.display["OPIS Truckstop ID"],
        display_name=aliases[0],
        aliases=aliases,
        address=first.display["Address"],
        city=first.display["City"],
        state=first.display["State"].upper(),
        country_code="US" if first.display["State"].upper() in US_STATES else "CA",
        rack_id=first.display["Rack ID"],
        selected_price=maximum,
        selected_price_text=selected_rows[0].display["Retail Price"],
        minimum_price_text=minimum_row.display["Retail Price"],
        maximum_price_text=selected_rows[0].display["Retail Price"],
        price_conflict=len(set(prices)) > 1,
        source_row_numbers=[row.number for row in rows],
        selected_price_row_numbers=[row.number for row in selected_rows],
        location_status="quarantined" if len(locations) > 1 else "unresolved",
    )


def import_prices(source: Path) -> tuple[DatasetVersion, bool]:
    """Return (published version, created). Invalid input leaves active data intact."""
    source = source.resolve(strict=True)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    rows, issues = _read_rows(source)
    if issues:
        raise ImportRejected(issues)
    groups: dict[str, list[ParsedRow]] = defaultdict(list)
    for row in rows:
        groups[row.display["OPIS Truckstop ID"]].append(row)
    if not groups:
        raise ImportRejected(("CSV has no usable station rows",))
    cross_country_ids = {
        opis_id
        for opis_id, group in groups.items()
        if len(
            {
                "US" if row.display["State"].upper() in US_STATES else "CA"
                for row in group
            }
        )
        > 1
    }
    if cross_country_ids:
        raise ImportRejected(
            (f"OPIS IDs cross country boundaries: {sorted(cross_country_ids)}",)
        )

    with transaction.atomic():
        existing = DatasetVersion.objects.filter(
            source_sha256=source_hash,
            normalization_policy=IMPORT_POLICY,
            coordinate_release="unresolved",
        ).first()
        if existing and existing.status == "active":
            return existing, False
        dataset = existing or DatasetVersion.objects.create(
            source_sha256=source_hash,
            source_filename=source.name,
            normalization_policy=IMPORT_POLICY,
            coordinate_release="unresolved",
        )
        if not existing:
            SourcePriceRow.objects.bulk_create(
                [
                    SourcePriceRow(
                        dataset=dataset,
                        source_row_number=row.number,
                        raw_fields=row.raw,
                        parsed_price_text=row.display["Retail Price"],
                        classification=row.classification,
                    )
                    for row in rows
                ],
                batch_size=1000,
            )
            stations = [_station_from_rows(dataset, group) for group in groups.values()]
            Station.objects.bulk_create(stations, batch_size=1000)
            dataset.counts = {
                "source_rows": len(rows),
                "us_rows": sum(
                    row.display["State"].upper() in US_STATES for row in rows
                ),
                "non_us_rows": sum(
                    row.display["State"].upper() not in US_STATES for row in rows
                ),
                "exact_duplicate_rows": sum(
                    row.classification.startswith("duplicate") for row in rows
                ),
                "station_ids": len(stations),
                "us_station_ids": sum(
                    station.country_code == "US" for station in stations
                ),
                "non_us_station_ids": sum(
                    station.country_code != "US" for station in stations
                ),
                "price_conflict_ids": sum(
                    station.price_conflict and station.country_code == "US"
                    for station in stations
                ),
                "all_price_conflict_ids": sum(
                    station.price_conflict for station in stations
                ),
                "quarantined_location_ids": sum(
                    station.location_status == "quarantined" for station in stations
                ),
                "rack_conflict_ids": sum(
                    len({canonical_text(row.display["Rack ID"]) for row in group}) > 1
                    for group in groups.values()
                ),
            }
        DatasetVersion.objects.filter(status="active").exclude(pk=dataset.pk).update(
            status="retired"
        )
        dataset.status = "active"
        dataset.save(update_fields=["counts", "status"])
    return dataset, existing is None
