from collections import Counter

import pytest

from scripts.build_csv_coordinate_catalog import OUTPUT, ROOT, validate_catalog


@pytest.mark.integration
@pytest.mark.skipif(
    not (ROOT / OUTPUT).exists(), reason="local GeoNames catalog missing"
)
def test_fallback_catalog_replays_and_keeps_precision_explicit() -> None:
    import json

    catalog = json.loads((ROOT / OUTPUT).read_text())
    validated = validate_catalog(ROOT, catalog["source_sha256"])
    records = validated["records"]
    assert len(records) == len({item["opis_id"] for item in records}) == 1967
    assert Counter(item["country"] for item in records) == {"US": 1906, "CA": 61}
    assert {item["location_precision"] for item in records} == {"city_representative"}
