from scripts.build_pilot_geography import build_seeds


def test_alias_and_ambiguity_are_explicit() -> None:
    sample = [
        {"opis_id": "1", "city": "Jacumba", "state": "CA"},
        {"opis_id": "2", "city": "Springfield", "state": "IL"},
    ]
    places = [
        {
            "id": "j",
            "name": "Jacumba Hot Springs",
            "aliases": ["Jacumba"],
            "state": "CA",
            "latitude": 32.6,
            "longitude": -116.2,
            "population": 500,
        },
        *[
            {
                "id": identifier,
                "name": "Springfield",
                "aliases": [],
                "state": "IL",
                "latitude": 39.8,
                "longitude": longitude,
                "population": 100,
            }
            for identifier, longitude in (("s1", -89.6), ("s2", -89.7))
        ],
    ]
    seeds = build_seeds(sample, places)
    assert seeds[0]["status"] == "alias"
    assert seeds[0]["centers"][0]["place_id"] == "j"
    assert seeds[1]["status"] == "ambiguous"
    assert len(seeds[1]["centers"]) == 2
