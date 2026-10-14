"""One bounded live API trip without printing the routing credential."""

import json
import os
import sys

from scripts.local_common import load_local_env

load_local_env()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from scripts.check_pilot_routes import routing_key  # noqa: E402


def main() -> None:
    os.environ["ORS_API_KEY"] = routing_key()
    if "--loves-corridor" in sys.argv:
        request = {
            "start": {"type": "coordinates", "latitude": 36.38, "longitude": -103.20},
            "finish": {"type": "city", "city": "Lamar", "state": "CO"},
            "initial_fuel_gallons": "3",
            "initial_fuel_price_per_gallon": "3.20",
        }
    elif "--station-case" in sys.argv:
        request = {
            "start": {"type": "coordinates", "latitude": 38.995, "longitude": -110.17},
            "finish": {"type": "city", "city": "Price", "state": "UT"},
            "initial_fuel_gallons": "1",
            "initial_fuel_price_per_gallon": "3.20",
        }
    else:
        request = {
            "start": {"type": "city", "city": "Chicago", "state": "IL"},
            "finish": {"type": "city", "city": "Joliet", "state": "IL"},
            "initial_fuel_gallons": "50",
            "initial_fuel_price_per_gallon": "3.20",
        }
    client = Client(HTTP_HOST="localhost")
    response = client.post(
        "/api/v1/trips", data=json.dumps(request), content_type="application/json"
    )
    data = response.json()
    if response.status_code != 200:
        print(
            json.dumps({"status": response.status_code, "error": data["error"]["code"]})
        )
        raise SystemExit(1)
    retrieved = client.get(
        f"/api/v1/trips/{data['trip_id']}",
        HTTP_AUTHORIZATION=f"Bearer {data['result_token']}",
    )
    if retrieved.status_code != 200:
        raise RuntimeError("saved result could not be retrieved")
    map_response = client.get(data["map_url"].split("#", 1)[0])
    if map_response.status_code != 200:
        raise RuntimeError("saved result map could not be opened")
    print(
        json.dumps(
            {
                "status": response.status_code,
                "trip_status": data["status"],
                "distance_miles": data["route"]["distance_miles"],
                "fuel_stops": len(data["fuel_stops"]),
                "station_ids": [stop["station_id"] for stop in data["fuel_stops"]],
                "retrieval_status": retrieved.status_code,
                "map_status": map_response.status_code,
            }
        )
    )


if __name__ == "__main__":
    main()
