from scripts.rank_pilot_candidates import candidate_rank


def _candidate(name: str, locality: str) -> dict:
    return {
        "id": f"{name}:{locality}",
        "name": name,
        "addresses": [
            {
                "freeform": "40 E Laraway Rd",
                "locality": locality,
                "region": "IL",
                "country": "US",
            }
        ],
        "basic_category": "gas_station",
        "point_wkt": "POINT (-88.079 41.480)",
    }


def test_local_generic_pilot_beats_similarly_named_distant_chain() -> None:
    station = {
        "aliases": ["PILOT TRAVEL CENTER #1024", "PILOT #1024"],
        "city": "Joliet",
        "state": "IL",
    }
    local_score, local = candidate_rank(station, _candidate("Pilot", "Joliet"))
    distant_score, distant = candidate_rank(
        station, _candidate("Pilot Travel Center", "Bridgeview")
    )
    assert local_score > distant_score
    assert local["locality_match"] is True
    assert distant["locality_match"] is False


def test_missing_address_region_is_skipped() -> None:
    station = {"aliases": ["Pilot #1024"], "city": "Joliet", "state": "IL"}
    candidate = _candidate("Pilot", "Joliet")
    candidate["addresses"][0]["region"] = None
    assert candidate_rank(station, candidate) == (-1.0, {})


def test_short_brand_name_gets_locality_credit() -> None:
    station = {"aliases": ["ONE9 #1271"], "city": "Thermal", "state": "CA"}
    candidate = _candidate("ONE9 Dealer", "Thermal")
    candidate["addresses"][0]["region"] = "CA"
    score, details = candidate_rank(station, candidate)
    assert score >= 1.0
    assert details["name_similarity"] == 0.5
    assert details["locality_match"] is True
