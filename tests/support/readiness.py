"""Small real assets and DB records for readiness and container smoke tests."""

import hashlib
import json
from pathlib import Path


def install_ready_assets(root: Path):
    from app.locations.models import GazetteerVersion, Place, PlaceAlias
    from app.stations.importer import US_STATES
    from app.stations.models import DatasetVersion, Station

    folder = root / "data/prepared/locations"
    candidates = root / "var/station_candidates"
    folder.mkdir(parents=True, exist_ok=True)
    candidates.mkdir(parents=True, exist_ok=True)
    boundary = {
        "states": [
            {
                "code": code,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-100, 40], [-99, 40], [-99, 41], [-100, 41], [-100, 40]]
                    ],
                },
            }
            for code in sorted(US_STATES)
        ]
    }
    outputs = {}
    for name, value in (
        ("states_coarse.geojson", boundary),
        ("states_detailed.geojson", boundary),
        ("places.ndjson", {}),
    ):
        raw = json.dumps(value).encode()
        (folder / name).write_bytes(raw)
        outputs[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "outputs": outputs,
                "sources": {"geonames_us": {"sha256": "place-source"}},
                "counts": {"populated_places": 1},
            }
        )
    )
    gazetteer = GazetteerVersion.objects.create(
        source_sha256="place-source",
        boundary_sha256=outputs["states_detailed.geojson"]["sha256"],
        status="active",
        place_count=1,
    )
    place = Place.objects.create(
        version=gazetteer,
        feature_id="one",
        name="Fixture",
        state="CO",
        latitude=40,
        longitude=-100,
    )
    PlaceAlias.objects.create(place=place, normalized_name="fixture", state="CO")
    dataset = DatasetVersion.objects.create(
        source_sha256="prices-source",
        source_filename="fixture.csv",
        normalization_policy="fixture",
        status="active",
        counts={"us_station_ids": 1, "station_ids": 1},
    )
    Station.objects.create(
        dataset=dataset,
        opis_id="one",
        display_name="Fixture",
        address="1 Test St",
        city="Fixture",
        state="CO",
        rack_id="1",
        selected_price="3.5",
        selected_price_text="3.5",
        minimum_price_text="3.5",
        maximum_price_text="3.5",
    )
    raw = json.dumps(
        [
            dict(
                station_id="one",
                name="Fixture",
                address="1 Test St",
                city="Fixture",
                state="CO",
                latitude=40,
                longitude=-100,
                price_per_gallon="3.5",
            )
        ]
    ).encode()
    (candidates / "candidates.json").write_bytes(raw)
    manifest = dict(
        source_sha256="prices-source",
        candidate_sha256=hashlib.sha256(raw).hexdigest(),
        candidate_count=1,
        total_us_station_ids=1,
        total_non_us_station_ids=0,
        total_csv_station_ids=1,
        linked_csv_station_ids=1,
        non_us_linked_station_ids=0,
        csv_coverage_percent=100.0,
    )
    (candidates / "manifest.json").write_text(json.dumps(manifest))
    return dataset, gazetteer, manifest
