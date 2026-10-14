import json
from decimal import Decimal

import pytest

from app.api.validation import parse_trip_request

POINT = {"type": "coordinates", "latitude": 41.0, "longitude": -87.0}


def _parse(**updates):
    return parse_trip_request(
        json.dumps({"start": POINT, "finish": POINT, **updates}).encode()
    )


def test_valid_partial_tank_and_decimal_price() -> None:
    trip = _parse(
        initial_fuel_gallons="12.5", initial_fuel_price_per_gallon="3.12345678"
    )
    assert str(trip.initial_fuel_gallons) == "12.5"
    assert str(trip.initial_fuel_price_per_gallon) == "3.12345678"
    assert trip.optimization_mode == "balanced"
    assert trip.max_extra_duration_minutes == 30
    assert trip.max_detour_miles is None
    assert trip.max_detour_ratio is None


def test_custom_travel_policy_is_parsed() -> None:
    trip = _parse(optimization_mode="balanced", max_extra_duration_minutes="12.5")
    assert trip.max_extra_duration_minutes == Decimal("12.5")
    lowest = _parse(optimization_mode="lowest_fuel_cost")
    assert lowest.optimization_mode == "lowest_fuel_cost"
    assert lowest.max_detour_miles is None
    assert lowest.max_detour_ratio is None
    custom = _parse(max_detour_miles="12", max_detour_ratio="0.2")
    assert custom.max_detour_miles == 12
    assert custom.max_detour_ratio == Decimal("0.2")
    miles_only = _parse(max_detour_miles="12")
    assert miles_only.max_detour_miles == 12
    assert miles_only.max_detour_ratio is None
    ratio_only = _parse(max_detour_ratio="0.2")
    assert ratio_only.max_detour_miles is None
    assert ratio_only.max_detour_ratio == Decimal("0.2")
    assert _parse(max_detour_miles="150").max_detour_miles == 150
    assert _parse(max_detour_ratio="0.75").max_detour_ratio == Decimal("0.75")
    assert _parse(max_extra_duration_minutes="30").max_extra_duration_minutes == 30


@pytest.mark.parametrize(
    "change",
    [
        {"range_miles": 600},
        {"initial_fuel_gallons": True},
        {"reserve_gallons": 50},
        {"initial_fuel_price_per_gallon": "NaN"},
        {"start": {"type": "coordinates", "latitude": True, "longitude": -87}},
        {"start": {"type": "city", "city": "Chicago", "state": "IL", "extra": 1}},
        {"optimization_mode": "cheapest"},
        {"max_extra_duration_minutes": "30.00000001"},
        {"max_extra_duration_minutes": True},
        {"initial_fuel_gallons": "-0.00000001"},
        {"initial_fuel_gallons": "50.00000001"},
        {"reserve_gallons": "49.99999999", "initial_fuel_gallons": "49.99999998"},
        {"initial_fuel_price_per_gallon": "0"},
        {"initial_fuel_price_per_gallon": "0.000000001"},
        {"max_detour_miles": float("nan")},
        {"max_detour_ratio": float("inf")},
        {"max_extra_duration_minutes": "-0.00000001"},
        {"initial_fuel_price_per_gallon": "1e13"},
        {"start": {**POINT, "latitude": "41"}},
        {"finish": {**POINT, "longitude": -180.00000001}},
        {"finish": {**POINT, "latitude": float("nan")}},
        {"optimization_mode": []},
        {"reserve_gallons": {}},
    ],
)
def test_invalid_trip_values_are_rejected(change) -> None:
    with pytest.raises(ValueError):
        _parse(**change)


@pytest.mark.parametrize(
    "body",
    [
        b'{"start":{"type":"coordinates","latitude":41,"longitude":-87},'
        b'"finish":{"type":"coordinates","latitude":41,"longitude":-87},'
        b'"initial_fuel_gallons":51,"initial_fuel_gallons":50}',
        b'{"start":{"type":"city","city":"one","city":"two","state":"IL"},'
        b'"finish":{"type":"coordinates","latitude":41,"longitude":-87}}',
    ],
)
def test_duplicate_json_fields_are_rejected(body):
    with pytest.raises(ValueError):
        parse_trip_request(body)


@pytest.mark.parametrize(
    "location",
    [
        {"type": "city", "city": "\ud800", "state": "IL"},
        {"type": "city", "city": "Chicago\x00", "state": "IL"},
        {"type": "place_id", "id": "\udfff"},
    ],
)
def test_unusable_location_text_is_rejected(location):
    with pytest.raises(ValueError):
        _parse(start=location)


@pytest.mark.parametrize(
    "updates",
    [
        {"initial_fuel_gallons": "0", "reserve_gallons": "0"},
        {"initial_fuel_gallons": "50", "reserve_gallons": "49.99999999"},
        {"initial_fuel_gallons": "1.00000001", "reserve_gallons": "1.00000001"},
        {"initial_fuel_price_per_gallon": "0.00000001"},
        {"initial_fuel_price_per_gallon": "9999999999999.99999999"},
        {
            "max_extra_duration_minutes": "0",
            "max_detour_ratio": "0",
            "max_detour_miles": "0",
        },
        {"start": {**POINT, "latitude": -90, "longitude": 180}},
        {"finish": {**POINT, "latitude": 90, "longitude": -180}},
        {"start": {"type": "city", "city": "São José", "state": "CA"}},
    ],
)
def test_valid_precision_and_range_edges_remain_supported(updates):
    assert _parse(**updates).initial_fuel_gallons >= Decimal(0)
