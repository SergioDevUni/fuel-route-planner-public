import json

import pytest

from app.stations.catalogs import green_river
from scripts.verify_green_river_case import (
    EVIDENCE,
    SOURCE,
    verify_links,
    verify_sites,
    verify_source_rows,
)


@pytest.mark.skipif(not SOURCE.exists(), reason="release CSV asset unavailable")
def test_reviewed_source_rows_match_frozen_assessment() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    verify_source_rows(evidence, SOURCE)
    changed = {
        **evidence,
        "source_rows": [dict(row) for row in evidence["source_rows"]],
    }
    changed["source_rows"][0]["store_number"] = "119"
    with pytest.raises(ValueError, match="differ"):
        verify_source_rows(changed, SOURCE)


def test_operator_site_verification_rejects_a_crossed_opis_link() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def page(site: dict, opis: str) -> str:
        store = {
            "name": f"Love's Store #{site['store_number']}",
            "telephone": site["phone"],
            "address": {
                "streetAddress": site["operator_address"],
                "addressLocality": site["city"],
                "addressRegion": site["state"],
                "addressCountry": "US",
            },
            "geo": {"latitude": site["latitude"], "longitude": site["longitude"]},
        }
        return (
            f'<script type="application/ld+json">{json.dumps(store)}</script>'
            f"<p>OPIS: {opis}</p>"
        )

    pages = {
        site["store_number"]: page(
            site, "70793" if site["store_number"] == "119" else "1914"
        )
        for site in evidence["sites"]
    }
    with pytest.raises(ValueError, match="page differs"):
        verify_sites(evidence, pages)


def test_csv_store_links_win_over_current_operator_opis(monkeypatch, tmp_path) -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        green_river,
        "read_codes",
        lambda _: [
            {
                "store_number": 792,
                "city": "Green River",
                "state": "UT",
                "opis_id": 70793,
            }
        ],
    )
    verify_links(evidence, tmp_path / "unused.xlsx")
    evidence["source_links"][0]["site_id"] = "loves:ut:green-river:119"
    with pytest.raises(ValueError, match="collapse|CSV store identity"):
        verify_links(evidence, tmp_path / "unused.xlsx")
