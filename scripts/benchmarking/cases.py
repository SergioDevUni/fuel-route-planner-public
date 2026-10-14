"""Frozen route inputs; importing this catalog does not initialize Django."""

from dataclasses import dataclass, replace

BASE_CASES = {
    "los_angeles_dallas": (
        {"type": "city", "city": "Los Angeles", "state": "CA"},
        {"type": "city", "city": "Dallas", "state": "TX"},
        "50",
    ),
    "seattle_denver": (
        {"type": "city", "city": "Seattle", "state": "WA"},
        {"type": "city", "city": "Denver", "state": "CO"},
        "50",
    ),
    "chicago_miami": (
        {"type": "city", "city": "Chicago", "state": "IL"},
        {"type": "city", "city": "Miami", "state": "FL"},
        "50",
    ),
    "new_york_los_angeles": (
        {"type": "city", "city": "New York", "state": "NY"},
        {"type": "city", "city": "Los Angeles", "state": "CA"},
        "50",
    ),
    "chicago_joliet": (
        {"type": "city", "city": "Chicago", "state": "IL"},
        {"type": "city", "city": "Joliet", "state": "IL"},
        "2",
    ),
    "green_river_price": (
        {"type": "coordinates", "latitude": 38.995, "longitude": -110.17},
        {"type": "city", "city": "Price", "state": "UT"},
        "1",
    ),
    "clayton_lamar": (
        {"type": "city", "city": "Clayton", "state": "NM"},
        {"type": "city", "city": "Lamar", "state": "CO"},
        "5",
    ),
    "chicago_st_louis": (
        {"type": "city", "city": "Chicago", "state": "IL"},
        {"type": "city", "city": "St. Louis", "state": "MO"},
        "5",
    ),
    "chicago_st_louis_full": (
        {"type": "city", "city": "Chicago", "state": "IL"},
        {"type": "city", "city": "St. Louis", "state": "MO"},
        "50",
    ),
    "chicago_indianapolis": (
        {"type": "city", "city": "Chicago", "state": "IL"},
        {"type": "city", "city": "Indianapolis", "state": "IN"},
        "5",
    ),
    "albuquerque_denver": (
        {"type": "city", "city": "Albuquerque", "state": "NM"},
        {"type": "city", "city": "Denver", "state": "CO"},
        "5",
    ),
    "san_diego_yuma": (
        {"type": "city", "city": "San Diego", "state": "CA"},
        {"type": "city", "city": "Yuma", "state": "AZ"},
        "10",
    ),
    "dallas_houston": (
        {"type": "city", "city": "Dallas", "state": "TX"},
        {"type": "city", "city": "Houston", "state": "TX"},
        "5",
    ),
    "phoenix_tucson": (
        {"type": "city", "city": "Phoenix", "state": "AZ"},
        {"type": "city", "city": "Tucson", "state": "AZ"},
        "10",
    ),
    "kansas_city_omaha": (
        {"type": "city", "city": "Kansas City", "state": "MO"},
        {"type": "city", "city": "Omaha", "state": "NE"},
        "10",
    ),
    "atlanta_chattanooga": (
        {"type": "city", "city": "Atlanta", "state": "GA"},
        {"type": "city", "city": "Chattanooga", "state": "TN"},
        "10",
    ),
    "portland_seattle": (
        {"type": "city", "city": "Portland", "state": "OR"},
        {"type": "city", "city": "Seattle", "state": "WA"},
        "10",
    ),
}

CASES = {
    **BASE_CASES,
    "chicago_joliet_full": (
        {"type": "city", "city": "Chicago", "state": "IL"},
        {"type": "city", "city": "Joliet", "state": "IL"},
        "50",
    ),
    "orlando_tampa": (
        {"type": "city", "city": "Orlando", "state": "FL"},
        {"type": "city", "city": "Tampa", "state": "FL"},
        "5",
    ),
    "orlando_jacksonville": (
        {"type": "city", "city": "Orlando", "state": "FL"},
        {"type": "city", "city": "Jacksonville", "state": "FL"},
        "5",
    ),
}


@dataclass(frozen=True)
class Case:
    start: dict
    finish: dict
    initial: str
    reserve: str = "0"
    held_out: bool = False


def city(name: str, state: str) -> dict:
    return {"type": "city", "city": name, "state": state}


BENCHMARK_CASES = {
    name: Case(*CASES[name])
    for name in (
        "chicago_joliet_full",
        "portland_seattle",
        "dallas_houston",
        "phoenix_tucson",
        "orlando_tampa",
        "atlanta_chattanooga",
        "clayton_lamar",
        "albuquerque_denver",
        "los_angeles_dallas",
        "seattle_denver",
        "chicago_miami",
        "new_york_los_angeles",
    )
}

BENCHMARK_CASES.update(
    {
        "boston_buffalo": Case(
            city("Boston", "MA"), city("Buffalo", "NY"), "20", held_out=True
        ),
        "pittsburgh_nashville": Case(
            city("Pittsburgh", "PA"), city("Nashville", "TN"), "15", held_out=True
        ),
        "salt_lake_boise": Case(
            city("Salt Lake City", "UT"), city("Boise", "ID"), "15", held_out=True
        ),
        "el_paso_austin": Case(
            city("El Paso", "TX"), city("Austin", "TX"), "30", held_out=True
        ),
        "minneapolis_rapid_city": Case(
            city("Minneapolis", "MN"), city("Rapid City", "SD"), "25", held_out=True
        ),
        "reno_sacramento_reserve": Case(
            city("Reno", "NV"), city("Sacramento", "CA"), "10", "2", True
        ),
        "raleigh_savannah": Case(
            city("Raleigh", "NC"),
            {"type": "place_id", "id": "geonames:4221552"},
            "15",
            held_out=True,
        ),
        "little_rock_memphis": Case(
            city("Little Rock", "AR"), city("Memphis", "TN"), "8", "1", True
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "santa_fe_oklahoma_city": Case(
            city("Santa Fe", "NM"), city("Oklahoma City", "OK"), "20", "2", True
        ),
        "las_vegas_san_diego": Case(
            city("Las Vegas", "NV"), city("San Diego", "CA"), "10", "1", True
        ),
        "san_antonio_new_orleans": Case(
            city("San Antonio", "TX"), city("New Orleans", "LA"), "20", held_out=True
        ),
        "detroit_cincinnati": Case(
            city("Detroit", "MI"), city("Cincinnati", "OH"), "15", held_out=True
        ),
        "boise_spokane": Case(
            city("Boise", "ID"), city("Spokane", "WA"), "20", held_out=True
        ),
        "charlotte_richmond": Case(
            city("Charlotte", "NC"), city("Richmond", "VA"), "10", "2", True
        ),
        "fargo_sioux_falls": Case(
            city("Fargo", "ND"), city("Sioux Falls", "SD"), "15", held_out=True
        ),
        "birmingham_jackson": Case(
            {"type": "place_id", "id": "geonames:4049979"},
            city("Jackson", "MS"),
            "8",
            "1",
            True,
        ),
        "dallas_houston_low_tank": replace(
            BENCHMARK_CASES["dallas_houston"], initial="2", reserve="1", held_out=True
        ),
        "portland_seattle_reserve": replace(
            BENCHMARK_CASES["portland_seattle"], initial="8", reserve="2", held_out=True
        ),
        "atlanta_chattanooga_full_tank": replace(
            BENCHMARK_CASES["atlanta_chattanooga"], initial="50", held_out=True
        ),
        "denver_albuquerque_reverse": Case(
            city("Denver", "CO"), city("Albuquerque", "NM"), "20", "3", True
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "louisville_st_louis": Case(
            city("Louisville", "KY"), city("St. Louis", "MO"), "12", "1", True
        ),
        "oakland_fresno": Case(
            city("Oakland", "CA"), city("Fresno", "CA"), "10", "2", True
        ),
        "wichita_amarillo": Case(
            city("Wichita", "KS"), city("Amarillo", "TX"), "11", "2", True
        ),
        "spokane_boise_reverse": Case(
            city("Spokane", "WA"), city("Boise", "ID"), "20", "0", True
        ),
        "richmond_raleigh_low_tank": Case(
            city("Richmond", "VA"), city("Raleigh", "NC"), "3", "1", True
        ),
        "cleveland_indianapolis": Case(
            city("Cleveland", "OH"), city("Indianapolis", "IN"), "17", "1", True
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "nashville_knoxville": Case(
            city("Nashville", "TN"), city("Knoxville", "TN"), "10", "1", True
        ),
        "milwaukee_madison": Case(
            city("Milwaukee", "WI"), city("Madison", "WI"), "5", "1", True
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "buffalo_syracuse": Case(
            city("Buffalo", "NY"), city("Syracuse", "NY"), "8", "1", True
        ),
        "reno_carson_city": Case(
            city("Reno", "NV"), city("Carson City", "NV"), "2", "1", True
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "cheyenne_laramie": Case(
            city("Cheyenne", "WY"), city("Laramie", "WY"), "8", "1", True
        ),
        "montgomery_mobile": Case(
            {"type": "place_id", "id": "geonames:4076784"},
            {"type": "place_id", "id": "geonames:4076598"},
            "15",
            "1",
            True,
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "lubbock_amarillo": Case(
            {"type": "place_id", "id": "geonames:5525577"},
            {"type": "place_id", "id": "geonames:5516233"},
            "4",
            "1",
            True,
        )
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "flagstaff_kingman": Case(
            {"type": "place_id", "id": "geonames:5294810"},
            {"type": "place_id", "id": "geonames:5301067"},
            "6",
            "1",
            True,
        )
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "richmond_charlottesville": Case(
            {"type": "place_id", "id": "geonames:4781708"},
            {"type": "place_id", "id": "geonames:4752031"},
            "4",
            "1",
            True,
        ),
        "meridian_hattiesburg": Case(
            {"type": "place_id", "id": "geonames:4435764"},
            {"type": "place_id", "id": "geonames:4429295"},
            "5",
            "1",
            True,
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "syracuse_albany": Case(
            {"type": "place_id", "id": "geonames:5140405"},
            {"type": "place_id", "id": "geonames:5106834"},
            "8",
            "1",
            True,
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "huntsville_tuscaloosa": Case(
            {"type": "place_id", "id": "geonames:4068590"},
            {"type": "place_id", "id": "geonames:4094455"},
            "6",
            "1",
            True,
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES.update(
    {
        "st_louis_springfield": Case(
            {"type": "place_id", "id": "geonames:4407066"},
            {"type": "place_id", "id": "geonames:4409896"},
            "8",
            "1",
            True,
        ),
    }
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES["amarillo_lubbock"] = Case(
    {"type": "place_id", "id": "geonames:5516233"},
    {"type": "place_id", "id": "geonames:5525577"},
    "6",
    "1",
    True,
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES["austin_waco"] = Case(
    {"type": "place_id", "id": "geonames:4671654"},
    {"type": "place_id", "id": "geonames:4739526"},
    "4",
    "1",
    True,
)

BENCHMARK_CASES = {
    name: replace(case, held_out=False) for name, case in BENCHMARK_CASES.items()
}

BENCHMARK_CASES["flint_saginaw"] = Case(
    {"type": "place_id", "id": "geonames:4992982"},
    {"type": "place_id", "id": "geonames:5007989"},
    "2",
    "0.5",
    True,
)
