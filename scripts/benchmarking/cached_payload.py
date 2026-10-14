"""Validate and preload a captured planning snapshot for offline HTTP measurements."""

import hashlib
import json
from decimal import Decimal

from app.api.validation import parse_trip_request
from scripts.benchmarking.contracts import validate_verified_trip
from scripts.local_common import sha256


def prepare_payload(
    probe, index, facts, initial_identity, payload_input, payload_output
):
    body = probe.body(0, True)
    trip = parse_trip_request(json.dumps(body).encode())
    key = probe.key(trip, None)[0]
    if payload_input:
        fixture = json.loads(payload_input.read_text(encoding="utf-8"))
        if (
            fixture["body"] != body
            or fixture["road_facts_sha256"] != sha256(facts)
            or fixture["candidate_sha256"] != index.version
        ):
            raise ValueError("Cached fixture input identity mismatch")
        probe.cache.put_completed(key, fixture["payload"])
    else:
        warmup = probe.wave(1, "cached_profile_warmup", same=True)
        if warmup["statuses"] != {200: 1}:
            raise ValueError("Captured plan warmup failed: " + json.dumps(warmup))
    payload = probe.cache.get_completed(key)
    errors = validate_verified_trip(
        {**payload, "map_url": "/validation-only"}, probe.csv_ids
    )
    prices = {v.station_id: v.price_per_gallon for v in index.stations}
    if (
        errors
        or any(
            Decimal(s["price_per_gallon"]) != prices[s["station_id"]]
            for s in payload["fuel_stops"]
        )
        or any(
            k in payload for k in ("result_token", "trip_id", "map_url", "expires_at")
        )
    ):
        raise ValueError("Cached payload failed independent validation")
    encoded = json.dumps(payload, sort_keys=True).encode()
    payload_identity = hashlib.sha256(encoded).hexdigest()
    if payload_output:
        payload_output.write_text(
            json.dumps(
                {
                    "body": body,
                    "payload": payload,
                    "road_facts_sha256": sha256(facts),
                    "candidate_sha256": index.version,
                    "generated_planner_sha256": initial_identity,
                }
            ),
            encoding="utf-8",
        )
    return payload_identity, len(encoded), key, payload
