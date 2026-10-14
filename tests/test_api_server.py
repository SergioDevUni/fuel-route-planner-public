"""Real production-server smoke and configuration rejection, without routing."""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration


def production_environment() -> dict[str, str]:
    return {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.production",
        "DJANGO_DEBUG": "0",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
    }


@pytest.mark.parametrize(
    "arguments,environment",
    [
        (["--threads", "7"], {}),
        (["--threads", "65"], {}),
        (["--trusted-proxy", "*"], {}),
        ([], {"DJANGO_SETTINGS_MODULE": "config.settings"}),
    ],
)
def test_server_rejects_unsafe_arguments_before_starting(arguments, environment):
    result = subprocess.run(
        [sys.executable, "-m", "scripts.serve_api", *arguments],
        env={**production_environment(), **environment},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert b"api_server_started" not in result.stderr


@pytest.mark.parametrize("trusted_proxy", ["127.0.0.1", "192.0.2.1"])
def test_actual_server_accepts_forwarded_identity_only_from_configured_proxy(
    trusted_proxy,
):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    code = """
import json, signal, threading
import config.wsgi
def application(environ, start_response):
    body = json.dumps({"caller": environ["REMOTE_ADDR"],
        "scheme": environ["wsgi.url_scheme"],
        "forwarded": environ.get("HTTP_X_FORWARDED_FOR")}).encode()
    start_response("200 OK", [("Content-Type", "application/json"),
        ("Content-Length", str(len(body)))])
    return [body]
config.wsgi.application = application
threading.Timer(3, lambda: signal.raise_signal(signal.SIGTERM)).start()
from scripts.serve_api import main
main()
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            "--listen",
            f"127.0.0.1:{port}",
            "--trusted-proxy",
            trusted_proxy,
        ],
        env=production_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            began = time.monotonic()
            while True:
                try:
                    response = client.get(
                        f"http://127.0.0.1:{port}/probe",
                        headers={
                            "X-Forwarded-For": "198.51.100.4, 203.0.113.7",
                            "X-Forwarded-Proto": "https",
                        },
                    )
                    break
                except httpx.TransportError:
                    if process.poll() is not None or time.monotonic() - began > 2.5:
                        raise AssertionError(
                            "Production server did not start"
                        ) from None
                    time.sleep(0.05)
            assert response.status_code == 200
            result = response.json()
            if trusted_proxy == "127.0.0.1":
                assert result["caller"] == "203.0.113.7"
                assert result["scheme"] == "https"
                assert result["forwarded"] == "203.0.113.7"
            else:
                assert result == {
                    "caller": "127.0.0.1",
                    "scheme": "http",
                    "forwarded": None,
                }
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr.decode()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_production_configuration_rejects_debug_and_wildcard_hosts() -> None:
    for change in (
        {"DJANGO_DEBUG": "1"},
        {"DJANGO_ALLOWED_HOSTS": "*"},
        {"DJANGO_SECRET_KEY": "short"},
    ):
        result = subprocess.run(
            [sys.executable, "-c", "import config.production"],
            env={**production_environment(), **change},
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert b"ImproperlyConfigured" in result.stderr


def test_waitress_serves_https_proxy_requests_and_shuts_down_cleanly() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    # Raise the stop signal inside the child: works with Windows pipe-only hosts.
    code = (
        "import signal, threading; "
        "threading.Timer(3, lambda: signal.raise_signal(signal.SIGTERM)).start(); "
        "from scripts.serve_api import main; main()"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            "--listen",
            f"127.0.0.1:{port}",
            "--trusted-proxy",
            "127.0.0.1",
        ],
        env=production_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(trust_env=False, timeout=1) as client:
            began = time.monotonic()
            while True:
                try:
                    response = client.get(
                        f"http://127.0.0.1:{port}/health/live",
                        headers={"X-Forwarded-Proto": "https"},
                    )
                    break
                except httpx.TransportError:
                    if process.poll() is not None or time.monotonic() - began > 2.5:
                        raise AssertionError(
                            "Production server did not become ready"
                        ) from None
                    time.sleep(0.05)
            assert response.status_code == 200
            assert response.json() == {"status": "live"}
            assert response.headers["x-request-id"]
            assert client.get(f"http://127.0.0.1:{port}/health/live").status_code == 200
            assert (
                client.get(f"http://127.0.0.1:{port}/api/v1/trips/example").status_code
                == 301
            )
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr.decode()
        assert b"api_shutdown_drained" in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_migrated_runtime_database_backup_restores_private_result(
    tmp_path: Path,
) -> None:
    original = tmp_path / "runtime.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    create = """
import os, sys
from pathlib import Path
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import config.settings
config.settings.DATABASES["default"]["NAME"] = sys.argv[1]
import django
django.setup()
from django.core.management import call_command
call_command("migrate", verbosity=0)
from django.utils import timezone
from app.api.models import TripResult
TripResult.objects.create(public_id="restore-evidence", token_hash="a" * 64,
    payload={"status": "verified"}, expires_at=timezone.now())
from scripts.backup_runtime_database import backup_database
backup_database(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    result = subprocess.run(
        [sys.executable, "-c", create, str(original), str(backup)],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode()

    shutil.copyfile(backup, restored)
    read = """
import os, sys
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import config.settings
config.settings.DATABASES["default"]["NAME"] = sys.argv[1]
import django
django.setup()
from app.api.models import TripResult
saved = TripResult.objects.get(public_id="restore-evidence")
assert saved.payload == {"status": "verified"}
from django.db import connection
with connection.cursor() as cursor:
    assert cursor.execute("PRAGMA integrity_check").fetchone() == ("ok",)
"""
    result = subprocess.run(
        [sys.executable, "-c", read, str(restored)], capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()


def test_production_database_lock_wait_fits_response_margin(tmp_path: Path) -> None:
    code = """
import sys, sqlite3, time
import config.production
config.production.DATABASES["default"]["NAME"] = sys.argv[1]
import django
django.setup()
from django.core.management import call_command
call_command("migrate", verbosity=0)
from django.db import connections, OperationalError
connections.close_all()
from app.api.models import TripResult
from django.utils import timezone
with sqlite3.connect(sys.argv[1]) as blocker:
    blocker.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        TripResult.objects.create(public_id="blocked", token_hash="a" * 64,
            payload={}, expires_at=timezone.now())
    except OperationalError:
        assert time.monotonic() - started < 1.5
    else:
        raise AssertionError("Contention should fail rather than wait or save")
    finally:
        blocker.rollback()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "runtime.sqlite3")],
        env=production_environment(),
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_real_server_drains_active_planner_before_closing_response() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    code = """
import signal, threading
import config.wsgi
from app.api.deadline import run_with_deadline
def application(environ, start_response):
    if environ["PATH_INFO"] == "/probe":
        body = b"ready"
    else:
        released = threading.Event()
        def work():
            threading.Timer(0.1, lambda: signal.raise_signal(signal.SIGTERM)).start()
            assert released.wait(3)
            return b"verified-result"
        body = run_with_deadline(work, 5, on_shutdown=released.set)
    start_response("200 OK", [("Content-Type", "text/plain"),
        ("Content-Length", str(len(body)))])
    return [body]
config.wsgi.application = application
from scripts.serve_api import main
main()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, "--listen", f"127.0.0.1:{port}"],
        env=production_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            began = time.monotonic()
            while True:
                try:
                    assert (
                        client.get(f"http://127.0.0.1:{port}/probe").status_code == 200
                    )
                    break
                except httpx.TransportError:
                    if process.poll() is not None or time.monotonic() - began > 5:
                        raise AssertionError("Server did not start") from None
                    time.sleep(0.05)
            response = client.get(f"http://127.0.0.1:{port}/active")
            assert response.status_code == 200
            assert response.content == b"verified-result"
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr.decode()
        assert b"api_shutdown_drained" in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
