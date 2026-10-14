"""Protect candidate ranking against same-number, wrong-city station matches."""

from scripts.rank_overture_state import score


def test_branch_number_cannot_override_conflicting_city() -> None:
    station = {
        "names": ["ROYAL FARMS #120"],
        "cities": ["Greenwood"],
        "addresses": ["US-13/CR-16"],
    }

    def place(name: str, city: str) -> dict:
        return {
            "id": name,
            "name": name,
            "addresses": [
                {
                    "country": "US",
                    "region": "DE",
                    "locality": city,
                    "freeform": "2 Schultz Rd"
                    if city == "Greenwood"
                    else "600 E Market St",
                }
            ],
            "point_wkt": "POINT (-75.58 38.8)",
            "basic_category": "gas_station",
            "operating_status": "open",
        }

    wrong = score(station, place("Royal Farms 120", "Georgetown"), "DE")
    plausible = score(station, place("Royal Farms", "Greenwood"), "DE")
    assert wrong is not None and plausible is not None
    assert wrong[1]["branch_number_match"]
    assert wrong[1]["city_conflict"]
    assert plausible[0] > wrong[0]
