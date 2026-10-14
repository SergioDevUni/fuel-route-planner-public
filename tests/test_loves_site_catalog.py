"""Exact source identity must survive operator-page coordinate extraction."""

import pytest

from scripts.build_loves_site_catalog import sitemap_stores, verify_page

ROW = {
    "opis_id": "135",
    "store_numbers": [766],
    "source_state": "IL",
    "source_city": "Atkinson",
}
URL = "https://www.loves.com/locations/il/atkinson/loves-travel-stop-atkinson-766"


def page(opis: str = "00135", state: str = "IL") -> bytes:
    return f"""
    <p>OPIS: {opis}</p>
    <script type="application/ld+json">{{
      "name":"Love's Travel Stop Store #766",
      "address":{{"streetAddress":"5 South State Street",
                  "addressLocality":"Atkinson","addressRegion":"{state}",
                  "addressCountry":"US"}},
      "geo":{{"latitude":41.410645,"longitude":-90.016349}}
    }}</script>
    """.encode()


def test_sitemap_and_exact_page_link() -> None:
    xml = f"<urlset><url><loc>{URL}</loc></url></urlset>".encode()
    assert sitemap_stores(xml) == {766: URL}
    result = verify_page(ROW, URL, page())
    assert result["opis_id"] == "135"
    assert result["latitude"] == 41.410645
    assert result["routing_status"] == "pending_check"


def test_workbook_link_allows_page_without_opis() -> None:
    result = verify_page(ROW, URL, page().replace(b"<p>OPIS: 00135</p>", b""))
    assert result["page_opis_status"] == "absent"


def test_csv_branch_resolves_current_opis_conflict() -> None:
    result = verify_page(ROW, URL, page(opis="70793"))
    assert result["page_opis_status"] == "conflict_csv_branch_authoritative"
    assert result["page_opis_ids"] == [70793]


def test_current_opis_conflict_needs_same_locality() -> None:
    wrong_city = page(opis="70793").replace(b"Atkinson", b"Green River")
    with pytest.raises(ValueError):
        verify_page(ROW, URL, wrong_city)


@pytest.mark.parametrize("body", [page(state="UT")])
def test_conflicting_operator_page_stays_unresolved(body: bytes) -> None:
    with pytest.raises(ValueError):
        verify_page(ROW, URL, body)
