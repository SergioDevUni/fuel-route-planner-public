import json

from scripts.verify_operator_coordinates import matching_store


def test_extracts_named_store_from_jsonld() -> None:
    document = {
        "name": "Love's Travel Stop. Store #749",
        "address": {"streetAddress": "551 West Main Street"},
        "geo": {"latitude": 33.037837, "longitude": -115.629103},
    }
    html = f'<script type="application/ld+json">{json.dumps(document)}</script>'
    store = matching_store(html, "749")
    assert store["geo"]["latitude"] == 33.037837
    assert store["address"]["streetAddress"] == "551 West Main Street"
