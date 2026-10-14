from scripts.build_pilot_site_catalog import family_branch, parse_page, reconcile


def test_partner_store_number_does_not_replace_flying_j_branch() -> None:
    assert family_branch("HUCKS #51 DBA FLYING J #889") == 889
    assert family_branch("PILOT TRAVEL CENTER #514 - THORNTONS #369") == 514


def test_operator_page_requires_branch_address_and_coordinates() -> None:
    url = "https://locations.pilotflyingj.com/us/il/joliet/40-e-laraway-rd"
    html = b"""
    <h1 itemprop="name">Pilot Travel Center #1024<br>Joliet, IL</h1>
    <meta itemprop="latitude" content="41.48">
    <meta itemprop="longitude" content="-88.08">
    <address data-country="US">
      <meta itemprop="addressLocality" content="Joliet">
      <meta itemprop="streetAddress" content="40 E Laraway Rd">
      <abbr itemprop="addressRegion">IL</abbr>
    </address>
    """
    page = parse_page(url, html)
    assert page["store_number"] == 1024
    assert page["brand"] == "pilot"
    assert page["address"] == "40 E Laraway Rd"
    assert page["latitude"] == 41.48


def test_duplicate_operator_point_keeps_distinct_csv_ids() -> None:
    page = {
        "site_id": "pilot-family:us:il:1024:pilot:abc",
        "store_number": 1024,
        "brand": "pilot",
        "city": "Joliet",
        "state": "IL",
    }
    source = {
        "1": {
            "brands": ["pilot"],
            "branches": [1024],
            "regions": ["IL"],
            "cities": ["joliet"],
        },
        "2": {
            "brands": ["pilot"],
            "branches": [1024],
            "regions": ["IL"],
            "cities": ["joliet"],
        },
        "3": {
            "brands": ["pilot"],
            "branches": [1024],
            "regions": ["IL"],
            "cities": ["chicago"],
        },
    }
    linked, unresolved = reconcile(source, [page])
    assert [item["opis_id"] for item in linked] == ["1", "2", "3"]
    assert {item["site_id"] for item in linked} == {page["site_id"]}
    assert unresolved == []


def test_unique_store_number_handles_neighboring_city_name() -> None:
    page = {
        "site_id": "pilot-family:us:mi:895:flying_j:abc",
        "store_number": 895,
        "brand": "flying_j",
        "city": "Woodhaven",
        "state": "MI",
    }
    source = {
        "7332": {
            "brands": ["flying_j"],
            "branches": [895],
            "regions": ["MI"],
            "cities": ["trenton"],
        }
    }
    linked, unresolved = reconcile(source, [page])
    assert [record["site_id"] for record in linked] == [page["site_id"]]
    assert unresolved == []
