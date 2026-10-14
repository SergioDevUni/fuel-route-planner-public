from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from app.domain.routing import Coordinates
from app.locations.boundary import BoundaryIndex

PREPARED = Path(__file__).resolve().parents[1] / "data/prepared/locations"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def boundaries() -> BoundaryIndex:
    return BoundaryIndex(PREPARED)


@pytest.mark.skipif(
    not (PREPARED / "manifest.json").exists(),
    reason="local prepared boundary assets unavailable",
)
@pytest.mark.parametrize(
    ("latitude", "longitude", "expected_state"),
    [
        (41.8781, -87.6298, "IL"),  # interior Lower 48
        (38.9, -77.04, "DC"),
        (47.6062, -122.3321, "WA"),
        (49.2827, -123.1207, None),  # nearby Canada
        (42.3314, -83.0458, "MI"),
        (42.3149, -83.0364, None),  # across the Detroit River
        (31.7619, -106.485, "TX"),
        (31.6904, -106.4245, None),  # across the Rio Grande
        (24.5551, -81.78, "FL"),  # Key West
        (25.0443, -77.3504, None),  # Bahamas
        (21.3099, -157.8581, "HI"),
        (19.7297, -155.09, "HI"),
        (58.3019, -134.4197, "AK"),
        (60.7212, -135.0568, None),  # Yukon
        (18.4655, -66.1057, None),  # Puerto Rico is outside 50 states/DC
        (18.3358, -64.8963, None),  # US Virgin Islands likewise
        (40.5, -73.0, None),  # offshore Atlantic
    ],
)
def test_prepared_us_boundary_examples(
    boundaries: BoundaryIndex,
    latitude: float,
    longitude: float,
    expected_state: str | None,
) -> None:
    assert boundaries.state_for(Coordinates(latitude, longitude)) == expected_state


@pytest.mark.skipif(
    not (PREPARED / "manifest.json").exists(),
    reason="local prepared boundary assets unavailable",
)
def test_shared_boundary_first_use_is_safe_across_request_threads() -> None:
    shared = BoundaryIndex(PREPARED)
    barrier = Barrier(12)

    def resolve(_: int) -> tuple[str | None, ...]:
        barrier.wait(timeout=2)
        return tuple(shared.state_for(Coordinates(40.7128, -74.006)) for _ in range(20))

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(resolve, range(12)))
    assert all(result == ("NY",) * 20 for result in results)
