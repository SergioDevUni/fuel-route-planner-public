import json
from argparse import ArgumentParser
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from app.stations.importer import ImportRejected, import_prices


class Command(BaseCommand):
    help = "Validate and atomically publish the assessment CSV price dataset"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("source", type=Path)
        parser.add_argument("--json-report", type=Path)

    def handle(self, *args: object, **options: object) -> None:
        source = options["source"]
        assert isinstance(source, Path)
        try:
            dataset, created = import_prices(source)
        except (OSError, ImportRejected) as error:
            if isinstance(error, ImportRejected):
                report_path = options["json_report"]
                if isinstance(report_path, Path):
                    report_path.write_text(
                        json.dumps(
                            {"status": "rejected", "issues": error.issues}, indent=2
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                raise CommandError(
                    f"Import rejected with {len(error.issues)} issue(s): {error}"
                ) from error
            raise CommandError(str(error)) from error
        report = {
            "dataset_id": dataset.pk,
            "source_sha256": dataset.source_sha256,
            "normalization_policy": dataset.normalization_policy,
            "status": dataset.status,
            "created": created,
            "counts": dataset.counts,
        }
        output = json.dumps(report, indent=2, sort_keys=True)
        report_path = options["json_report"]
        if isinstance(report_path, Path):
            report_path.write_text(output + "\n", encoding="utf-8")
        self.stdout.write(output)
