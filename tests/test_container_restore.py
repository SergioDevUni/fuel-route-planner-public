"""Exercise candidate asset restore, HTTP smoke and restart in a fresh checkout."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from scripts.local_common import ROOT

pytestmark = pytest.mark.integration


def test_container_install_path_restores_assets_and_preserves_saved_trips(tmp_path):
    root, bundle = tmp_path / "checkout", tmp_path / "bundle"
    root.mkdir()
    for name in ("app", "config", "scripts", "tests"):
        shutil.copytree(
            ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    for name in ("manage.py", "pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, root / name)
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_DEBUG": "1",
        "ORS_API_KEY": "offline-sentinel",
        "FUEL_CONTAINER_BUNDLE": str(bundle / "assets.zip"),
    }
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.prepare_container_fixture",
            "--output",
            str(bundle),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert created.returncode == 0, created.stderr
    shutil.copy2(bundle / "local-release.json", root / "local-release.json")
    measured = subprocess.run(
        [sys.executable, "-m", "scripts.verify_container_install"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert measured.returncode == 0, measured.stdout + measured.stderr
    report = json.loads(measured.stdout[measured.stdout.index("{\n") :])
    assert report["passed"] and report["hosted_attempts"] == 0
    assert report["restore_restart_saved_access_map_static_shutdown"]
