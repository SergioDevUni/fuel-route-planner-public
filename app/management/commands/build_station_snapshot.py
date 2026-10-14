"""Publish an immutable candidate snapshot from validated site reviews."""

import json
from argparse import ArgumentParser
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.stations.files import atomic_write
from app.stations.models import DatasetVersion, Station
from app.stations.snapshot import build_candidate_snapshot


class Command(BaseCommand):
    help = "Build reviewed station site candidates for runtime route verification"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--output-dir", type=Path)

    def handle(self, *args: object, **options: object) -> None:
        root = Path(settings.BASE_DIR)
        output_dir = options["output_dir"] or root / "var/station_candidates"
        dataset = DatasetVersion.objects.filter(status="active").first()
        if dataset is None:
            raise CommandError("import the price source first")
        stations = list(
            Station.objects.filter(dataset=dataset, country_code="US").values(
                "opis_id",
                "display_name",
                "address",
                "city",
                "state",
                "selected_price_text",
            )
        )
        try:
            payload, manifest = build_candidate_snapshot(
                root, dataset.source_sha256, stations
            )
        except (OSError, KeyError, ValueError) as error:
            raise CommandError(str(error)) from error
        atomic_write(output_dir / "candidates.json", payload)
        atomic_write(
            output_dir / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
        self.stdout.write(json.dumps(manifest, indent=2, sort_keys=True))
