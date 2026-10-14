from argparse import ArgumentParser
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from app.locations.importer import import_places


class Command(BaseCommand):
    help = "Atomically publish a prepared US GeoNames gazetteer"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("folder", type=Path)

    def handle(self, *args: object, **options: object) -> None:
        folder = options["folder"]
        assert isinstance(folder, Path)
        try:
            version, created = import_places(folder)
        except (OSError, ValueError, KeyError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            f"Gazetteer {version.pk}: {version.place_count} places; "
            f"created={created}; status={version.status}"
        )
