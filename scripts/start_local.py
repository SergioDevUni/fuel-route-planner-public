"""Load private local configuration and serve the API on loopback with Waitress."""

import argparse
import json
import os
import sys

from scripts.local_common import ROOT, load_local_env, verify_installed


def preflight() -> dict:
    load_local_env()
    if (
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        != "config.settings"
    ):
        raise ValueError(
            "Local start requires config.settings; use scripts.serve_api for production"
        )
    manifest = verify_installed()
    if not os.environ.get("ORS_API_KEY", "").strip():
        raise ValueError(
            "Set ORS_API_KEY in .env. Live trips use your own openrouteservice key."
        )
    changed = [
        key
        for key, expected in manifest["hosted_settings"].items()
        if os.environ.get(key, expected) != expected
    ]
    if changed:
        print("Using custom planning configuration: " + ", ".join(changed))
        print("The release data is preserved; this profile may return different plans.")
    import django

    django.setup()
    from app.api.readiness import application_ready

    if not application_ready(full=True):
        raise ValueError(
            "Readiness failed. Check the data bundle and routing configuration."
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--check", action="store_true", help="Verify readiness without starting HTTP"
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024-65535")
    try:
        manifest = preflight()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f"Local start failed: {error}") from None
    if args.check:
        print(
            json.dumps(
                {"status": "ready", "version": manifest["version"], "root": str(ROOT)}
            )
        )
        return
    print(f"API: http://127.0.0.1:{args.port}", flush=True)
    print(f"Health: http://127.0.0.1:{args.port}/health/ready", flush=True)
    print("Stop with Ctrl+C. Prepared data is preserved.", flush=True)
    from scripts.serve_api import main as serve

    sys.argv = [sys.argv[0], "--listen", f"127.0.0.1:{args.port}"]
    serve(local=True)


if __name__ == "__main__":
    main()
