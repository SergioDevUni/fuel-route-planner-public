from django.test import TestCase

from app.domain.location import LocationAmbiguous, LocationNotFound
from app.domain.routing import Coordinates
from app.locations.catalog import DjangoPlaceCatalog
from app.locations.models import GazetteerVersion, Place, PlaceAlias
from app.locations.resolver import LocationResolver


class _IllinoisBoundary:
    def state_for(self, point: Coordinates) -> str | None:
        return "IL" if point.longitude < -80 else None


class LocationLookupTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.version = GazetteerVersion.objects.create(
            source_sha256="a" * 64, boundary_sha256="b" * 64, status="active"
        )
        for number, latitude in (("1", 39.8), ("2", 39.9)):
            place = Place.objects.create(
                version=cls.version,
                feature_id=f"geonames:{number}",
                name="Springfield",
                state="IL",
                latitude=latitude,
                longitude=-89.6,
            )
            PlaceAlias.objects.create(
                place=place, normalized_name="springfield", state="IL"
            )

    def test_ambiguous_name_requires_explicit_place_id(self) -> None:
        resolver = LocationResolver(
            _IllinoisBoundary(), DjangoPlaceCatalog(self.version)
        )
        with self.assertRaises(LocationAmbiguous) as captured:
            resolver.city("Springfield", "Illinois")
        self.assertEqual(len(captured.exception.candidates), 2)
        self.assertEqual(resolver.place_id("geonames:1").precision, "city")

    def test_unknown_name_and_foreign_coordinates_rejected(self) -> None:
        resolver = LocationResolver(
            _IllinoisBoundary(), DjangoPlaceCatalog(self.version)
        )
        with self.assertRaises(LocationNotFound):
            resolver.city("Unknown", "IL")
        with self.assertRaises(ValueError):
            resolver.coordinates(Coordinates(43.65, -79.38))
