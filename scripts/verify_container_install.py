"""Offline restore/restart smoke with actual HTTP, saved access and map assets."""

import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx

from scripts.container_start import prepare
from scripts.local_common import ROOT, planner_hash, verify_installed


def start_server(port):
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.serve_api",
            "--listen",
            f"127.0.0.1:{port}",
            "--trusted-proxy",
            "127.0.0.1",
        ],
        env={
            **environment,
            "DJANGO_SETTINGS_MODULE": "config.production",
            "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": "container-offline-verification-secret-"
            "with-adequate-length-12345",
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    return process


def stop_server(process):
    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
    try:
        process.wait(timeout=35)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise AssertionError("container API did not drain") from None
    if process.returncode != 0:
        raise AssertionError("container API exited without a clean drain")


def smoke(port, previous=None):
    process = start_server(port)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False
        ) as client:
            until = time.monotonic() + 20
            while True:
                try:
                    ready = client.get("/health/ready")
                    if ready.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() >= until or process.poll() is not None:
                    raise AssertionError("container API did not become ready")
                time.sleep(0.1)
            # TLS forwarding is accepted only from the explicitly trusted loopback.
            # The smoke process uses its configured trusted proxy for API requests.
            if previous is None:
                point = {"type": "coordinates", "latitude": 40.5, "longitude": -99.5}
                response = client.post(
                    "/api/v1/trips",
                    json={"start": point, "finish": point},
                    headers={"X-Forwarded-Proto": "https"},
                )
                if response.status_code != 200:
                    raise AssertionError("zero-distance offline trip failed")
                previous = response.json()
            headers = {
                "Authorization": "Bearer " + previous["result_token"],
                "X-Forwarded-Proto": "https",
            }
            saved = client.get("/api/v1/trips/" + previous["trip_id"], headers=headers)
            if saved.status_code != 200:
                raise AssertionError("saved trip did not survive restart")
            page = client.get(
                "/maps/" + previous["trip_id"], headers={"X-Forwarded-Proto": "https"}
            )
            if page.status_code != 200 or "trip_map" not in page.text:
                raise AssertionError("saved map failed")
            for path in (
                "/static/app/trip_map.js",
                "/static/app/trip_map.css",
                "/static/vendor/leaflet/leaflet.js",
            ):
                if (
                    client.get(path, headers={"X-Forwarded-Proto": "https"}).status_code
                    != 200
                ):
                    raise AssertionError("map static asset failed")
        return previous
    finally:
        stop_server(process)


def main():
    prepare("local")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    first = smoke(port)
    prepare("local")
    smoke(port, first)
    manifest = verify_installed()
    result = {
        "passed": True,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "planner_sha256": planner_hash(ROOT),
        "asset_identity": manifest["dataset"],
        "hosted_attempts": 0,
        "restore_restart_saved_access_map_static_shutdown": True,
        "finished_utc": datetime.now(UTC).isoformat(),
        "scope": "Zero-distance HTTP smoke; prepared assets verified; "
        "no live route quality claim",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
