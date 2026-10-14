"""Strict public trip input validation with no provider calls."""

import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.routing import Coordinates


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON fields are not supported")
        result[name] = value
    return result


def _valid_text(value: Any, minimum: int, maximum: int) -> bool:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return all(character.isprintable() for character in value)


@dataclass(frozen=True, slots=True)
class TripInput:
    start: dict[str, Any]
    finish: dict[str, Any]
    initial_fuel_gallons: Decimal
    initial_fuel_price_per_gallon: Decimal | None
    reserve_gallons: Decimal
    max_detour_miles: Decimal | None
    max_detour_ratio: Decimal | None
    optimization_mode: str = "balanced"
    max_extra_duration_minutes: Decimal = Decimal(30)


def _decimal(value: Any, name: str, low: Decimal, high: Decimal | None) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{name} must be a decimal number")
    if isinstance(value, str) and (len(value) > 32 or not value.strip()):
        raise ValueError(f"{name} must be a decimal number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a decimal number") from error
    if not number.is_finite() or number < low or (high is not None and number > high):
        raise ValueError(f"{name} is outside its accepted range")
    exponent = number.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -8 or number.adjusted() > 12:
        raise ValueError(f"{name} exceeds supported numeric precision")
    return number


def resolve_location(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a location object")
    kind = value.get("type")
    keys = {
        "coordinates": {"type", "latitude", "longitude"},
        "city": {"type", "city", "state"},
        "place_id": {"type", "id"},
    }
    if not isinstance(kind, str) or kind not in keys or set(value) != keys[kind]:
        raise ValueError(f"{name} has an unsupported location format")
    if kind == "coordinates":
        for field, lower, upper in (("latitude", -90, 90), ("longitude", -180, 180)):
            item = value[field]
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not lower <= item <= upper
                or not math.isfinite(item)
            ):
                raise ValueError(f"{name}.{field} must be a number")
        Coordinates(value["latitude"], value["longitude"])
    elif kind == "city":
        if not _valid_text(value["city"], 1, 200):
            raise ValueError(f"{name}.city must be nonempty and at most 200 characters")
        if not _valid_text(value["state"], 2, 40):
            raise ValueError(f"{name}.state is invalid")
    elif not _valid_text(value["id"], 1, 64):
        raise ValueError(f"{name}.id is invalid")
    return value


def parse_trip_request(body: bytes) -> TripInput:
    if len(body) > 16 * 1024:
        raise ValueError("request body exceeds 16 KiB")
    try:
        raw = json.loads(body, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ValueError("invalid JSON body") from error
    if not isinstance(raw, dict):
        raise ValueError("request body must be an object")
    allowed = {
        "start",
        "finish",
        "initial_fuel_gallons",
        "initial_fuel_price_per_gallon",
        "reserve_gallons",
        "max_detour_miles",
        "max_detour_ratio",
        "optimization_mode",
        "max_extra_duration_minutes",
    }
    if set(raw) - allowed or not {"start", "finish"} <= set(raw):
        raise ValueError("unknown or missing trip fields")
    initial = _decimal(
        raw.get("initial_fuel_gallons", 50),
        "initial_fuel_gallons",
        Decimal(0),
        Decimal(50),
    )
    reserve = _decimal(
        raw.get("reserve_gallons", 0),
        "reserve_gallons",
        Decimal(0),
        Decimal(50),
    )
    if reserve >= 50 or reserve > initial:
        raise ValueError("reserve_gallons must be below capacity and initial fuel")
    mode = raw.get("optimization_mode", "balanced")
    if mode not in ("balanced", "lowest_fuel_cost"):
        raise ValueError("optimization_mode is invalid")
    extra_minutes = _decimal(
        raw.get("max_extra_duration_minutes", 30),
        "max_extra_duration_minutes",
        Decimal(0),
        Decimal(30),
    )
    price = raw.get("initial_fuel_price_per_gallon")
    return TripInput(
        start=resolve_location(raw["start"], "start"),
        finish=resolve_location(raw["finish"], "finish"),
        initial_fuel_gallons=initial,
        initial_fuel_price_per_gallon=(
            _decimal(
                price, "initial_fuel_price_per_gallon", Decimal("0.00000001"), None
            )
            if price is not None
            else None
        ),
        reserve_gallons=reserve,
        max_detour_miles=(
            _decimal(raw["max_detour_miles"], "max_detour_miles", Decimal(0), None)
            if "max_detour_miles" in raw
            else None
        ),
        max_detour_ratio=(
            _decimal(raw["max_detour_ratio"], "max_detour_ratio", Decimal(0), None)
            if "max_detour_ratio" in raw
            else None
        ),
        optimization_mode=mode,
        max_extra_duration_minutes=extra_minutes,
    )
