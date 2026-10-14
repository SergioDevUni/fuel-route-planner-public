import json

import duckdb

from scripts import fetch_overture_pilot
from scripts.fetch_overture_pilot import box_filter, boxes_for_center


def test_box_around_midlatitude_center() -> None:
    boxes = boxes_for_center(42.0, -88.0)
    assert len(boxes) == 1
    west, south, east, north = boxes[0]
    assert west < -88.0 < east
    assert south < 42.0 < north
    assert "bbox.xmin BETWEEN" in box_filter(boxes)
    assert "bbox.xmax" not in box_filter(boxes)


def test_dateline_crossing_box_is_split() -> None:
    boxes = boxes_for_center(52.0, 179.9)
    assert len(boxes) == 2
    assert boxes[0][2] == 180.0
    assert boxes[1][0] == -180.0
    assert all(-180.0 <= west < east <= 180.0 for west, _, east, _ in boxes)


def test_station_alias_is_included_as_literal_regex(tmp_path, monkeypatch) -> None:
    sample = tmp_path / "var/pilot/stations.json"
    sample.parent.mkdir(parents=True)
    sample.write_text(
        json.dumps({"stations": [{"opis_id": "1", "aliases": ["O'Brien #4"]}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fetch_overture_pilot, "ROOT", tmp_path)
    pattern = fetch_overture_pilot.station_name_filter("1")
    assert "O'Brien\\ \\#4" in pattern
    assert duckdb.sql(
        "SELECT regexp_matches('O''Brien #4', ?)", params=[pattern]
    ).fetchone() == (True,)
