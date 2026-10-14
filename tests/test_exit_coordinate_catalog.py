import json

import pytest

from scripts.build_exit_coordinate_catalog import (
    OUTPUT,
    ROOT,
    road_and_exits,
    validate_catalog,
)


def test_csv_interstate_and_suffix_are_parsed_without_dropping_branch() -> None:
    assert road_and_exits("I-95, EXIT 329") == ({"I-95"}, ["329"])
    assert road_and_exits("I-75, EXIT 144-B") == ({"I-75"}, ["144B"])
    assert road_and_exits("I-70, EXIT 193/US-40") == ({"I-70"}, ["193"])
    assert road_and_exits("I-75, EXIT 290/US-41") == ({"I-75"}, ["290"])
    assert road_and_exits("I-55, EXIT 52/SR-P") == ({"I-55"}, ["52"])
    assert road_and_exits("I-20/59, EXIT 100") == (
        {"I-20", "I-59"},
        ["100"],
    )


@pytest.mark.integration
@pytest.mark.skipif(not (ROOT / OUTPUT).exists(), reason="local exit release missing")
def test_pinned_exit_links_replay_from_openinterstate() -> None:
    catalog = json.loads((ROOT / OUTPUT).read_text())
    records = validate_catalog(ROOT, catalog["source_sha256"])["records"]
    assert len(records) == len({item["opis_id"] for item in records})
    assert len(records) >= 785
    assert {item["location_precision"] for item in records} == {"interstate_exit"}
    by_id = {item["opis_id"]: item for item in records}
    assert by_id["56"]["interstate_name"] == "I-94"
    assert by_id["56"]["exit_number"] == "182"
    assert by_id["105"]["exit_number"] == "144B"
