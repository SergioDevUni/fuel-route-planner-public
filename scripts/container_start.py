"""Initialize pinned assets and run one API process in an explicit container mode."""

import json
import os
import sys
from pathlib import Path

from scripts.local_common import ROOT, verify_installed
from scripts.setup_local import install_bundle


def prepare(mode: str, *, root: Path = ROOT, bundle: Path | None = None):
    if mode not in ("local", "production"):
        raise ValueError("FUEL_CONTAINER_MODE must be local or production")
    module = "config.settings" if mode == "local" else "config.production"
    if os.environ.get("DJANGO_SETTINGS_MODULE", module) != module:
        raise ValueError("Django settings differ from the explicit container mode")
    os.environ["DJANGO_SETTINGS_MODULE"] = module
    bundle = bundle or Path(
        os.environ.get("FUEL_CONTAINER_BUNDLE", "/opt/fuel/assets.zip")
    )
    descriptor = json.loads((root / "local-release.json").read_text())
    receipt = root / "var/LOCAL_ASSETS_MANIFEST.json"
    if receipt.exists():
        manifest = json.loads(receipt.read_text())
        if (
            manifest["version"] != descriptor["version"]
            or manifest["planner_sha256"] != descriptor["planner_sha256"]
        ):
            raise ValueError("Persistent assets belong to another candidate")
    # Restore absent immutable files and preserve an existing result database.
    install_bundle(root, bundle, descriptor)
    verify_installed(root)
    import django

    django.setup()
    from django.core.management import call_command

    from app.api.readiness import application_ready

    call_command("migrate", interactive=False, verbosity=0)
    call_command("collectstatic", interactive=False, verbosity=0)
    call_command("check", verbosity=0)
    if not application_ready(full=True):
        raise ValueError("Container prerequisites failed validation")


def main():
    mode = os.environ.get("FUEL_CONTAINER_MODE", "local")
    try:
        prepare(mode)
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f"Container startup failed: {error}") from None
    from scripts.serve_api import main as serve

    sys.argv = [sys.argv[0], "--listen", "0.0.0.0:8000"]
    proxy = os.environ.get("FUEL_TRUSTED_PROXY", "").strip()
    if proxy:
        sys.argv += ["--trusted-proxy", proxy]
    serve(local=mode == "local")


if __name__ == "__main__":
    main()
