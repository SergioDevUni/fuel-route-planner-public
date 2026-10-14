"""Version road geometry independently of mutable CSV fuel offers."""

import hashlib
import json
from collections.abc import Iterable

from app.domain.routing import Coordinates

SCHEMA_VERSION = "csv-station-edges-v2-auto"


def point_set_hash(points: Iterable[Coordinates]) -> str:
    """Identify physical coordinates without station IDs, prices, or text."""
    pairs = sorted(
        {(float(point.latitude), float(point.longitude)) for point in points}
    )
    payload = json.dumps(pairs, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()
