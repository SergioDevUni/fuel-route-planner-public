from scripts.build_ta_site_catalog import operator_brand, reconcile, title_key


def test_only_ta_operator_brands_are_included() -> None:
    assert operator_brand("TA EXPRESS SUMMIT") == "ta_express"
    assert operator_brand("PETRO STOPPING CENTER #306") == "petro"
    assert operator_brand("PETRO-CARD 24") is None
    assert title_key("TA SEYMOUR TRAVEL CENTER") == title_key("TA Seymour")


def test_numbered_petro_uses_site_id_and_operator_locality() -> None:
    source = {
        "121": {
            "brands": ["petro"],
            "states": ["AZ"],
            "site_numbers": [306],
            "cities": ["eloy"],
            "title_keys": ["306"],
        }
    }
    site = {
        "site_id": "ta:306",
        "site_number": 306,
        "brand": "petro",
        "state": "AZ",
        "city": "Casa Grande",
        "name": "Petro Eloy",
    }
    linked, unresolved = reconcile(source, [site])
    assert [row["opis_id"] for row in linked] == ["121"]
    assert unresolved == []


def test_unique_operator_title_survives_exit_municipality_difference() -> None:
    source = {
        "341": {
            "brands": ["ta"],
            "states": ["FL"],
            "site_numbers": [],
            "cities": ["saint johns"],
            "title_keys": [title_key("TA JACKSONVILLE SOUTH TRAVEL CENTER")],
        }
    }
    site = {
        "site_id": "ta:248",
        "site_number": 248,
        "brand": "ta",
        "state": "FL",
        "city": "Jacksonville",
        "name": "TA Jacksonville South",
    }
    linked, unresolved = reconcile(source, [site])
    assert [row["site_id"] for row in linked] == ["ta:248"]
    assert unresolved == []


def test_title_fallback_requires_a_unique_site() -> None:
    source = {
        "341": {
            "brands": ["ta"],
            "states": ["FL"],
            "site_numbers": [],
            "cities": ["saint johns"],
            "title_keys": [title_key("TA JACKSONVILLE SOUTH TRAVEL CENTER")],
        }
    }
    site = {
        "site_id": "ta:248",
        "site_number": 248,
        "brand": "ta",
        "state": "FL",
        "city": "Jacksonville",
        "name": "TA Jacksonville South",
    }
    second = {**site, "site_id": "ta:999", "site_number": 999}
    linked, unresolved = reconcile(source, [site, second])
    assert linked == []
    assert unresolved == [{"opis_id": "341", "reason": "no_unique_operator_site"}]


def test_distinct_csv_ids_can_use_one_operator_point() -> None:
    identity = {
        "brands": ["ta"],
        "states": ["FL"],
        "site_numbers": [],
        "cities": ["saint johns"],
        "title_keys": [title_key("TA Jacksonville South")],
    }
    site = {
        "site_id": "ta:248",
        "site_number": 248,
        "brand": "ta",
        "state": "FL",
        "city": "Jacksonville",
        "name": "TA Jacksonville South",
    }
    linked, unresolved = reconcile({"1": identity, "2": identity}, [site])
    assert [record["opis_id"] for record in linked] == ["1", "2"]
    assert [record["site_id"] for record in linked] == ["ta:248", "ta:248"]
    assert unresolved == []
