import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from app.stations.access_review import record_selected_locality_stops
from app.stations.importer import HEADER, ImportRejected, import_prices
from app.stations.models import (
    DatasetVersion,
    SelectedLocationReview,
    SourcePriceRow,
    Station,
)
from app.stations.snapshot import build_candidate_snapshot

SOURCE_FILE = Path(settings.BASE_DIR) / "data/raw/fuel-prices-for-be-assessment.csv"


class SelectedLocationReviewTests(TestCase):
    def test_selected_locality_is_queued_and_purchases_are_counted(self) -> None:
        trip = SimpleNamespace(
            candidate_version="a" * 64,
            routed_stations=(
                SimpleNamespace(
                    station_id="127", location_precision="city_representative"
                ),
                SimpleNamespace(station_id="202", location_precision="site_candidate"),
            ),
            fuel=SimpleNamespace(
                visits=(
                    SimpleNamespace(purchased_gallons=Decimal("2.5")),
                    SimpleNamespace(purchased_gallons=Decimal("1")),
                )
            ),
            verified=SimpleNamespace(
                waypoints=(
                    None,
                    SimpleNamespace(snap_distance_meters=Decimal("18.1234")),
                    SimpleNamespace(snap_distance_meters=Decimal("3")),
                    None,
                )
            ),
        )
        record_selected_locality_stops(trip)
        record_selected_locality_stops(trip)
        self.assertEqual(SelectedLocationReview.objects.count(), 1)
        review = SelectedLocationReview.objects.get(opis_id="127")
        self.assertEqual(review.selected_count, 2)
        self.assertEqual(review.purchased_count, 2)
        self.assertEqual(review.last_snap_meters, Decimal("18.123"))
        self.assertEqual(review.last_purchase_gallons, Decimal("2.50000000"))

        reviewed_at = timezone.now()
        review.review_status = "location_only"
        review.review_note = "CSV locality retained after access review"
        review.reviewed_at = reviewed_at
        review.save(update_fields=["review_status", "review_note", "reviewed_at"])
        record_selected_locality_stops(trip)
        review.refresh_from_db()
        self.assertEqual(review.selected_count, 3)
        self.assertEqual(review.purchased_count, 3)
        self.assertEqual(review.review_status, "location_only")
        self.assertEqual(
            review.review_note, "CSV locality retained after access review"
        )
        self.assertEqual(review.reviewed_at, reviewed_at)


@skipUnless(SOURCE_FILE.exists(), "assessment CSV is local-only")
class PriceImportTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.source = SOURCE_FILE
        cls.dataset, _ = import_prices(cls.source)

    def test_audited_counts_and_price_checkedance(self) -> None:
        self.assertEqual(self.dataset.counts["source_rows"], 8151)
        self.assertEqual(self.dataset.counts["us_station_ids"], 6626)
        self.assertEqual(self.dataset.counts["non_us_station_ids"], 112)
        self.assertEqual(self.dataset.counts["station_ids"], 6738)
        self.assertEqual(Station.objects.filter(dataset=self.dataset).count(), 6738)
        self.assertEqual(
            Station.objects.filter(dataset=self.dataset, country_code="CA").count(),
            112,
        )
        self.assertEqual(self.dataset.counts["price_conflict_ids"], 487)
        self.assertEqual(self.dataset.counts["exact_duplicate_rows"], 26)
        self.assertEqual(SourcePriceRow.objects.count(), 8151)
        station = Station.objects.get(dataset=self.dataset, opis_id="105")
        self.assertEqual(station.selected_price_text, "3.429")
        self.assertTrue(station.price_conflict)
        self.assertTrue(station.selected_price_row_numbers)
        for row_number in station.selected_price_row_numbers:
            row = SourcePriceRow.objects.get(
                dataset=self.dataset, source_row_number=row_number
            )
            self.assertEqual(row.parsed_price_text, "3.429")

    def test_repeat_and_invalid_import_preserve_active_version(self) -> None:
        repeated, created = import_prices(self.source)
        self.assertFalse(created)
        self.assertEqual(repeated.pk, self.dataset.pk)
        with TemporaryDirectory() as folder:
            invalid = Path(folder) / "bad.csv"
            invalid.write_text(
                ",".join(HEADER) + "\n123,Example,1 Road,City,TX,1,not-a-price\n",
                encoding="utf-8",
            )
            with self.assertRaises(ImportRejected):
                import_prices(invalid)
        self.assertEqual(DatasetVersion.objects.count(), 1)
        self.assertEqual(
            DatasetVersion.objects.get(pk=self.dataset.pk).status, "active"
        )


class FixtureImportTests(TestCase):
    def test_price_policy_duplicate_and_location_quarantine(self) -> None:
        with TemporaryDirectory() as folder:
            source = Path(folder) / "fixture.csv"
            source.write_text(
                ",".join(HEADER)
                + "\n1,Alpha,1 Road,City,TX,7,3.10"
                + "\n1,Alpha,1 Road,City,TX,7,3.10"
                + "\n1,Alpha,1 Road,City,TX,7,3.30"
                + "\n2,Beta,2 Road,City,TX,8,4.00"
                + "\n2,Beta,9 Road,City,TX,8,4.00"
                + "\n3,Foreign,3 Road,City,ON,9,5.00\n",
                encoding="utf-8",
            )
            dataset, created = import_prices(source)
            self.assertTrue(created)
            self.assertEqual(dataset.counts["source_rows"], 6)
            self.assertEqual(dataset.counts["exact_duplicate_rows"], 1)
            self.assertEqual(dataset.counts["price_conflict_ids"], 1)
            self.assertEqual(dataset.counts["quarantined_location_ids"], 1)
            self.assertEqual(dataset.counts["us_station_ids"], 2)
            self.assertEqual(dataset.counts["non_us_station_ids"], 1)
            self.assertEqual(dataset.counts["station_ids"], 3)
            self.assertEqual(
                Station.objects.get(opis_id="3").country_code,
                "CA",
            )
            self.assertEqual(
                Station.objects.get(opis_id="1").selected_price_text, "3.30"
            )
            self.assertEqual(
                Station.objects.get(opis_id="2").location_status, "quarantined"
            )

    def test_one_opis_id_cannot_span_countries(self) -> None:
        with TemporaryDirectory() as folder:
            source = Path(folder) / "cross-country.csv"
            source.write_text(
                ",".join(HEADER)
                + "\n1,Alpha,1 Road,City,TX,7,3.10"
                + "\n1,Alpha,1 Road,City,ON,7,3.10\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ImportRejected, "cross country"):
                import_prices(source)


class CandidateSnapshotTests(TestCase):
    def test_csv_locality_point_is_labelled_in_candidate(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            audit = root / "data/audit"
            audit.mkdir(parents=True)
            (audit / "source-profile.json").write_text(
                json.dumps(
                    {
                        "sha256": "source",
                        "unique_station_ids": 1,
                        "us_unique_station_ids": 1,
                        "non_us_unique_station_ids": 0,
                    }
                )
            )
            stations = [
                {
                    "opis_id": "101",
                    "display_name": "CSV fuel offer",
                    "address": "I-95, EXIT 329",
                    "city": "Saint Johns",
                    "state": "FL",
                    "selected_price_text": "3.50",
                }
            ]
            reviews = {
                "101": {
                    "status": "csv_location_coordinate",
                    "site_id": "geonames:123",
                }
            }
            sites = {
                "101": {
                    "latitude": 30.0661,
                    "longitude": -81.4961,
                }
            }
            with (
                patch(
                    "app.stations.snapshot.load_reviews",
                    return_value=(reviews, {"101"}, {}),
                ),
                patch("app.stations.snapshot._read_sites", return_value=sites),
            ):
                payload, manifest = build_candidate_snapshot(root, "source", stations)
        records = json.loads(payload)
        self.assertEqual(records[0]["location_precision"], "city_representative")
        self.assertEqual(records[0]["price_per_gallon"], "3.50")
        self.assertEqual(manifest["city_representative_station_ids"], 1)
        self.assertEqual(manifest["site_candidate_station_ids"], 0)

    def test_same_site_point_keeps_two_csv_prices(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            audit = root / "data/audit"
            audit.mkdir(parents=True)
            (audit / "source-profile.json").write_text(
                json.dumps(
                    {
                        "sha256": "source",
                        "unique_station_ids": 2,
                        "us_unique_station_ids": 2,
                        "non_us_unique_station_ids": 0,
                    }
                )
            )
            stations = [
                {
                    "opis_id": station_id,
                    "display_name": f"Station {station_id}",
                    "address": "I-95, EXIT 329",
                    "city": "Saint Johns",
                    "state": "FL",
                    "selected_price_text": price,
                }
                for station_id, price in (("101", "4.00"), ("202", "3.00"))
            ]
            reviews = {
                station_id: {
                    "status": "operator_coordinate",
                    "site_id": "operator:shared",
                }
                for station_id in ("101", "202")
            }
            sites = {
                station_id: {
                    "site_id": "operator:shared",
                    "latitude": 30.0661,
                    "longitude": -81.4961,
                }
                for station_id in ("101", "202")
            }
            with (
                patch(
                    "app.stations.snapshot.load_reviews",
                    return_value=(reviews, set(reviews), {}),
                ),
                patch("app.stations.snapshot._read_sites", return_value=sites),
            ):
                payload, manifest = build_candidate_snapshot(root, "source", stations)
        records = json.loads(payload)
        self.assertEqual(manifest["candidate_count"], 2)
        self.assertEqual([record["station_id"] for record in records], ["101", "202"])
        self.assertEqual(
            [record["price_per_gallon"] for record in records], ["4.00", "3.00"]
        )
        self.assertEqual({record["site_id"] for record in records}, {"operator:shared"})

    def test_canadian_site_counts_toward_coverage_without_entering_us_routes(
        self,
    ) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            audit = root / "data/audit"
            audit.mkdir(parents=True)
            (audit / "source-profile.json").write_text(
                json.dumps(
                    {
                        "sha256": "source",
                        "unique_station_ids": 2,
                        "us_unique_station_ids": 1,
                        "non_us_unique_station_ids": 1,
                    }
                )
            )
            stations = [
                {
                    "opis_id": "1",
                    "display_name": "US stop",
                    "address": "Main St",
                    "city": "Joliet",
                    "state": "IL",
                    "selected_price_text": "3.50",
                }
            ]
            reviews = {
                "1": {"status": "operator_coordinate", "site_id": "us:1"},
                "2": {"status": "operator_coordinate", "site_id": "ca:2"},
            }
            sites = {"1": {"latitude": 41.0, "longitude": -88.0}}
            with (
                patch(
                    "app.stations.snapshot.load_reviews",
                    return_value=(reviews, {"1", "2"}, {}),
                ),
                patch("app.stations.snapshot._read_sites", return_value=sites),
            ):
                payload, manifest = build_candidate_snapshot(root, "source", stations)
        self.assertEqual(len(json.loads(payload)), 1)
        self.assertEqual(manifest["candidate_count"], 1)
        self.assertEqual(manifest["linked_csv_station_ids"], 2)
        self.assertEqual(manifest["non_us_linked_station_ids"], 1)
        self.assertEqual(manifest["csv_coverage_percent"], 100.0)
