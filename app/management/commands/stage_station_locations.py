import hashlib
import json
from argparse import ArgumentParser
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.stations.accounting import (
    REQUIRED_CSV_SITE_COVERAGE_PERCENT,
    build_location_accounting,
)
from app.stations.catalogs.csv_location_catalog import (
    build_catalog as build_csv_location_catalog,
)
from app.stations.files import atomic_write
from app.stations.models import DatasetVersion, Station
from app.stations.reviews import load_reviews

"""Build complete, non-published T05 location status accounting."""


class Command(BaseCommand):
    help = (
        "Stage complete US station-location accounting without publishing coordinates"
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--output-dir", type=Path)

    def handle(self, *args: object, **options: object) -> None:
        root = Path(settings.BASE_DIR)
        output_dir = options["output_dir"] or root / "var/station_locations"
        assert isinstance(output_dir, Path)
        dataset = DatasetVersion.objects.filter(status="active").first()
        if dataset is None:
            raise CommandError("import the price source before staging locations")
        try:
            reviews, verified_ids, input_hashes = load_reviews(
                root, dataset.source_sha256
            )
            stations = list(
                Station.objects.filter(dataset=dataset).values(
                    "opis_id", "state", "location_status"
                )
            )
            if len(stations) != dataset.counts["station_ids"]:
                raise ValueError("station count differs from imported price data")
            profile = json.loads((root / "data/audit/source-profile.json").read_text())
            if (
                profile["sha256"] != dataset.source_sha256
                or dataset.counts["non_us_station_ids"]
                != profile["non_us_unique_station_ids"]
            ):
                raise ValueError("non-US identity count differs from audited CSV")
            rows, summary = build_location_accounting(stations, reviews, verified_ids)
            if len(rows) != profile["unique_station_ids"]:
                raise ValueError("accounting count differs from imported price dataset")
            csv_catalog_path = root / "var/station_locations/csv_location_catalog.json"
            csv_catalog_bytes = csv_catalog_path.read_bytes()
            csv_catalog = json.loads(csv_catalog_bytes)
            if csv_catalog != build_csv_location_catalog(
                root / "data/raw/fuel-prices-for-be-assessment.csv",
                root / "data/audit/source-profile.json",
            ):
                raise ValueError("CSV address catalog differs from source")
            csv_by_id = {record["opis_id"]: record for record in csv_catalog["records"]}
            if len(csv_by_id) != len(rows) or {row["opis_id"] for row in rows} != set(
                csv_by_id
            ):
                raise ValueError("CSV address links differ from accounting IDs")
            for row in rows:
                csv_location = csv_by_id[row["opis_id"]]
                row["csv_address"] = csv_location["address"]
                row["csv_city"] = csv_location["city"]
                row["csv_station_names"] = csv_location["names"]
                row["csv_location_link_status"] = "linked"
        except (OSError, KeyError, ValueError) as error:
            raise CommandError(str(error)) from error
        output = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in rows
        )
        manifest = {
            "schema_version": 2,
            "status": "staged_not_route_ready",
            "release_decision": "blocked_insufficient_accepted_coverage",
            "accepted_station_ids": 0,
            "accepted_coverage_percent": 0.0,
            "minimum_accepted_percent": REQUIRED_CSV_SITE_COVERAGE_PERCENT,
            "total_csv_station_ids": profile["unique_station_ids"],
            "csv_address_linked_station_ids": len(rows),
            "csv_location_catalog_sha256": hashlib.sha256(
                csv_catalog_bytes
            ).hexdigest(),
            "total_us_station_ids": profile["us_unique_station_ids"],
            "total_non_us_station_ids": profile["non_us_unique_station_ids"],
            "source_sha256": dataset.source_sha256,
            "normalization_policy": dataset.normalization_policy,
            "inputs": input_hashes,
            "accounting_sha256": hashlib.sha256(output).hexdigest(),
            "accounting_bytes": len(output),
            **summary,
        }
        atomic_write(output_dir / "accounting.ndjson", output)
        atomic_write(
            output_dir / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
        self.stdout.write(json.dumps(manifest, indent=2, sort_keys=True))
