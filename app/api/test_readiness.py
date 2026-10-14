"""Readiness exercises real prerequisites and fails closed on damaged installs."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings

from app.api import readiness
from app.locations.models import Place, PlaceAlias
from app.stations.models import Station
from tests.support.readiness import install_ready_assets


class ReadinessTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = override_settings(BASE_DIR=self.root)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.environment = patch.dict(
            "os.environ",
            {"ORS_API_KEY": "offline-sentinel", "FUEL_NATIONAL_LOCAL_ROUTER": "0"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.dataset, self.gazetteer, self.manifest = install_ready_assets(self.root)

    def ready(self, expected):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, expected)
        self.assertEqual(
            response.json(), {"status": "ready" if expected == 200 else "not_ready"}
        )
        self.assertEqual(self.client.get("/health/live").status_code, 200)

    def test_real_install_and_cached_poll(self):
        with patch.object(
            readiness, "_validate_assets", wraps=readiness._validate_assets
        ) as validate:
            self.ready(200)
            self.ready(200)
            self.assertEqual(validate.call_count, 1)
            self.assertTrue(readiness.application_ready(full=True))
            self.assertEqual(validate.call_count, 2)

    def test_missing_or_corrupt_files_invalidate_success(self):
        for relative in (
            "var/station_candidates/candidates.json",
            "data/prepared/locations/states_detailed.geojson",
            "data/prepared/locations/places.ndjson",
            "data/prepared/locations/manifest.json",
        ):
            with self.subTest(path=relative):
                path = self.root / relative
                original = path.read_bytes()
                self.ready(200)
                path.unlink()
                self.ready(503)
                path.write_bytes(b"corrupt")
                self.ready(503)
                path.write_bytes(original)
                self.ready(200)

    def test_manifest_only_is_not_a_ready_install(self):
        (self.root / "var/station_candidates/candidates.json").unlink()
        self.ready(503)

    def test_missing_active_versions_and_prepared_records(self):
        self.ready(200)
        for model in (self.dataset, self.gazetteer):
            model.status = "retired"
            model.save()
            self.ready(503)
            model.status = "active"
            model.save()
        PlaceAlias.objects.all().delete()
        self.ready(503)
        Place.objects.all().delete()
        self.ready(503)

    def test_database_offer_price_mismatch(self):
        Station.objects.update(selected_price="4")
        self.ready(503)

    def test_identity_mismatch(self):
        self.gazetteer.boundary_sha256 = "wrong"
        self.gazetteer.save()
        self.ready(503)

    def test_malformed_coverage_preserves_liveness(self):
        path = self.root / "var/station_candidates/manifest.json"
        for broken in (
            None,
            [],
            {},
            {**self.manifest, "source_sha256": "wrong"},
            {**self.manifest, "total_csv_station_ids": 2},
            *(
                {**self.manifest, "csv_coverage_percent": value}
                for value in (None, "100", 99.99, float("inf"), float("nan"), 100.01)
            ),
        ):
            with self.subTest(manifest=broken):
                path.write_text(json.dumps(broken))
                self.ready(503)

    def test_draining_and_invalid_configuration(self):
        with patch.object(
            readiness, "planning_activity", return_value={"accepting_work": False}
        ):
            self.ready(503)
        with patch.dict(
            "os.environ", {"ORS_API_KEY": "", "FUEL_NATIONAL_LOCAL_ROUTER": "0"}
        ):
            self.ready(503)
        with patch.dict("os.environ", {"FUEL_MATRIX_GRAPH": "invalid"}):
            self.ready(503)

    def test_local_only_needs_matching_completed_checkpoint(self):
        database = self.root / "var/road_nationwide/station_edges.db"
        database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database)) as db, db:
            db.executescript(
                "CREATE TABLE metadata(key,value); CREATE TABLE point(point_id); "
                "CREATE TABLE complete_source(point_id); "
                "CREATE TABLE edge(source_id,target_id,"
                "distance_meters,duration_seconds); "
                "INSERT INTO point VALUES(1); INSERT INTO complete_source VALUES(1);"
            )
            db.executemany(
                "INSERT INTO metadata VALUES(?,?)",
                [
                    ("schema", "csv-station-edges-v1-auto"),
                    ("road_graph_version", "road-v1"),
                    ("candidate_hash", self.manifest["candidate_sha256"]),
                ],
            )
        marker = database.with_suffix(".complete.json")
        marker.write_text(
            json.dumps(
                {
                    "graph_version": "road-v1",
                    "candidate_version": self.manifest["candidate_sha256"],
                }
            )
        )
        with patch.dict(
            "os.environ",
            {
                "ORS_API_KEY": "",
                "FUEL_NATIONAL_LOCAL_ROUTER": "1",
                "FUEL_NATIONAL_VALHALLA_URL": "http://127.0.0.1:8004",
                "FUEL_NATIONAL_VALHALLA_VERSION": "road-v1",
            },
        ):
            self.ready(200)
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("DELETE FROM complete_source")
            self.ready(503)
            marker.unlink()
            self.ready(503)
