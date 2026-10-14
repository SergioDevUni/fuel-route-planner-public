"""Local state-boundary validation with pinned, detailed Census polygons."""

import hashlib
import json
from pathlib import Path
from threading import Lock

from shapely.geometry import Point, shape
from shapely.prepared import prep

from app.domain.routing import Coordinates


class BoundaryIndex:
    def __init__(self, folder: Path) -> None:
        # GEOS prepared predicates lazily mutate internal indexes. Sharing the
        # immutable polygon does not make concurrent first-use predicates safe.
        self._predicate_guard = Lock()
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        boundary_file = folder / "states_detailed.geojson"
        expected = manifest["outputs"][boundary_file.name]["sha256"]
        if hashlib.sha256(boundary_file.read_bytes()).hexdigest() != expected:
            raise ValueError("detailed boundary asset hash mismatch")
        document = json.loads(boundary_file.read_text(encoding="utf-8"))
        self._states = tuple(
            (entry["code"], polygon.bounds, prep(polygon))
            for entry in document["states"]
            for polygon in [shape(entry["geometry"])]
        )
        if len(self._states) != 51:
            raise ValueError("expected 50 state polygons plus DC")

    def state_for(self, coordinates: Coordinates) -> str | None:
        point = Point(coordinates.longitude, coordinates.latitude)
        for code, bounds, prepared in self._states:
            west, south, east, north = bounds
            if west <= point.x <= east and south <= point.y <= north:
                with self._predicate_guard:
                    if prepared.covers(point):
                        return code
        return None
