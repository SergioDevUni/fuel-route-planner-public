"""Publish a pinned local GeoNames place index after asset validation."""

import hashlib
import json
from pathlib import Path

from django.db import transaction

from app.domain.routing import Coordinates
from app.domain.text import canonical_text
from app.locations.models import GazetteerVersion, Place, PlaceAlias
from app.stations.importer import US_STATES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_manifest(folder: Path) -> tuple[dict[str, object], Path]:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    outputs = manifest["outputs"]
    for filename, info in outputs.items():
        path = folder / filename
        if not path.is_file() or _sha256(path) != info["sha256"]:
            raise ValueError(f"prepared location asset hash mismatch: {filename}")
    if manifest["counts"]["coarse_states"] != 51:
        raise ValueError("coarse US state coverage is incomplete")
    if manifest["counts"]["detailed_states"] != 51:
        raise ValueError("detailed US state coverage is incomplete")
    return manifest, folder / "places.ndjson"


def import_places(folder: Path) -> tuple[GazetteerVersion, bool]:
    manifest, source = _validated_manifest(folder)
    source_hash = manifest["sources"]["geonames_us"]["sha256"]
    boundary_hash = manifest["outputs"]["states_detailed.geojson"]["sha256"]
    with transaction.atomic():
        existing = GazetteerVersion.objects.filter(source_sha256=source_hash).first()
        if existing and existing.status == "active":
            return existing, False
        version = existing or GazetteerVersion.objects.create(
            source_sha256=source_hash,
            boundary_sha256=boundary_hash,
        )
        if not existing:
            pending: list[tuple[Place, tuple[str, ...]]] = []
            count = 0
            with source.open(encoding="utf-8") as stream:
                for line in stream:
                    raw = json.loads(line)
                    if raw["state"] not in US_STATES:
                        raise ValueError("non-US place in prepared data")
                    point = Coordinates(raw["latitude"], raw["longitude"])
                    aliases = tuple(
                        dict.fromkeys(canonical_text(name) for name in raw["aliases"])
                    )
                    if not aliases:
                        raise ValueError("place has no names")
                    pending.append(
                        (
                            Place(
                                version=version,
                                feature_id=raw["id"],
                                name=raw["name"],
                                state=raw["state"],
                                latitude=point.latitude,
                                longitude=point.longitude,
                                population=raw["population"],
                            ),
                            aliases,
                        )
                    )
                    if len(pending) >= 1000:
                        _flush_places(pending)
                        count += len(pending)
                        pending.clear()
            if pending:
                _flush_places(pending)
                count += len(pending)
            if count != manifest["counts"]["populated_places"]:
                raise ValueError("prepared place count mismatch")
            version.place_count = count
        GazetteerVersion.objects.filter(status="active").exclude(pk=version.pk).update(
            status="retired"
        )
        version.status = "active"
        version.save(update_fields=["status", "place_count"])
    return version, existing is None


def _flush_places(pending: list[tuple[Place, tuple[str, ...]]]) -> None:
    places = Place.objects.bulk_create(
        [place for place, _aliases in pending], batch_size=1000
    )
    aliases = [
        PlaceAlias(place=place, normalized_name=name, state=place.state)
        for place, (_original, names) in zip(places, pending, strict=True)
        for name in names
    ]
    PlaceAlias.objects.bulk_create(aliases, batch_size=2000)
