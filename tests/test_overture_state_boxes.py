"""Alaska's antimeridian geometry must not trigger a world-wide place scan."""

import json

import pytest

from scripts import build_overture_state_catalog
from scripts.build_overture_state_catalog import state_search_boxes


@pytest.fixture(autouse=True)
def synthetic_boundaries(tmp_path, monkeypatch):
    def rectangle(west, south, east, north):
        return [
            [west, south],
            [east, south],
            [east, north],
            [west, north],
            [west, south],
        ]

    path = tmp_path / "states.geojson"
    path.write_text(
        json.dumps(
            {
                "states": [
                    {
                        "code": "AK",
                        "geometry": {
                            "type": "MultiPolygon",
                            "coordinates": [
                                [rectangle(-170, 52, -130, 71)],
                                [rectangle(175, 51, 180, 55)],
                            ],
                        },
                    },
                    {
                        "code": "DE",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [rectangle(-76, 38, -75, 40)],
                        },
                    },
                ]
            }
        )
    )
    monkeypatch.setattr(build_overture_state_catalog, "BOUNDS", path)


def test_alaska_uses_two_bounded_search_boxes() -> None:
    boxes = state_search_boxes("AK")
    assert len(boxes) == 2
    assert all(east - west < 90 for west, _, east, _ in boxes)
    assert any(west < -149 < east for west, _, east, _ in boxes)
    assert any(west < 179 < east for west, _, east, _ in boxes)


def test_non_antimeridian_state_uses_one_box() -> None:
    boxes = state_search_boxes("DE")
    assert len(boxes) == 1
