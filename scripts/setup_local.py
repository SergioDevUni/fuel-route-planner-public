"""Restore pinned release data without rematching stations or replacing results."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

from scripts.local_common import (
    MANIFEST,
    ROOT,
    asset_path,
    load_local_env,
    planner_hash,
    sha256,
    verify_installed,
)


def download_bundle(root: Path, descriptor: dict) -> Path:
    destination = root / "var/downloads" / descriptor["asset"]
    if destination.is_file():
        return destination
    gh = shutil.which("gh")
    if gh is None:
        raise ValueError(
            "Install GitHub CLI and run gh auth login, or download the release "
            "asset manually and pass --bundle <path>."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            gh,
            "release",
            "download",
            descriptor["version"],
            "--repo",
            descriptor["repository"],
            "--pattern",
            descriptor["asset"],
            "--dir",
            str(destination.parent),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise ValueError(
            "Release download failed. Check gh auth status and repository access, "
            "or use --bundle with a downloaded release asset."
        )
    return destination


def install_bundle(root: Path, bundle: Path, descriptor: dict) -> dict:
    if planner_hash(root) != descriptor["planner_sha256"]:
        raise ValueError("Checkout/dependency lock differs from this data release")
    if (
        bundle.stat().st_size != descriptor["bytes"]
        or sha256(bundle) != descriptor["sha256"]
    ):
        raise ValueError("Data bundle checksum mismatch; no assets were installed")
    with (
        tempfile.TemporaryDirectory(prefix="fuel-assets-") as raw,
        ZipFile(bundle) as archive,
    ):
        staged = Path(raw)
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or MANIFEST not in names:
            raise ValueError("Archive has duplicate entries or no manifest")
        manifest_entry = archive.getinfo(MANIFEST)
        if manifest_entry.file_size > 2 * 1024 * 1024:
            raise ValueError("Asset manifest exceeds its size limit")
        manifest = json.loads(archive.read(MANIFEST))
        if (
            manifest["schema"] != 1
            or manifest["version"] != descriptor["version"]
            or manifest["planner_sha256"] != descriptor["planner_sha256"]
            or manifest["dataset"] != descriptor["dataset"]
            or manifest["private_results_included"] is not False
            or set(names) != {*manifest["contents"], MANIFEST}
        ):
            raise ValueError("Bundle manifest differs from the pinned release")
        for entry in entries:
            if entry.filename == MANIFEST:
                continue
            target = asset_path(root, entry.filename)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or entry.is_dir():
                raise ValueError("Asset archive cannot contain links or directories")
            record = manifest["contents"][entry.filename]
            if entry.file_size != record["bytes"]:
                raise ValueError("Asset size differs from its manifest")
            path = staged / entry.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with archive.open(entry) as source, path.open("xb") as destination:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    destination.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != record["sha256"]:
                raise ValueError("Asset checksum differs from its manifest")
            if target.exists() and (
                not target.is_file()
                or (
                    entry.filename != "var/app.sqlite3"
                    and sha256(target) != record["sha256"]
                )
            ):
                raise ValueError(
                    "Existing asset differs; preserved without replacement: "
                    + entry.filename
                )
        # All checks complete before any asset installation. Never replace a DB.
        for name in manifest["contents"]:
            target = asset_path(root, name)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=".fuel-install-", dir=target.parent, delete=False
            ) as destination:
                temporary = Path(destination.name)
                try:
                    with (staged / name).open("rb") as source:
                        shutil.copyfileobj(source, destination)
                except BaseException:
                    destination.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                # Atomic publication without replacing a file created by another setup.
                os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        receipt = root / "var" / MANIFEST
        receipt.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
    return manifest


def initialize_environment(root: Path) -> None:
    destination = root / ".env"
    if destination.exists():
        return
    content = (root / ".env.example").read_text(encoding="utf-8")
    content = content.replace(
        "DJANGO_SECRET_KEY=\n", "DJANGO_SECRET_KEY=" + secrets.token_urlsafe(48) + "\n"
    )
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(content)


def setup(root: Path = ROOT, bundle: Path | None = None) -> dict:
    descriptor = json.loads((root / "local-release.json").read_text(encoding="utf-8"))
    receipt = root / "var" / MANIFEST
    if receipt.is_file() and bundle is None:
        manifest = verify_installed(root)
        if manifest["version"] != descriptor["version"]:
            raise ValueError(
                "Installed data differs from the requested release version"
            )
    else:
        manifest = install_bundle(
            root, bundle or download_bundle(root, descriptor), descriptor
        )
    initialize_environment(root)
    load_local_env(root)
    module = os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if module != "config.settings":
        raise ValueError(
            "Local setup requires config.settings; use a separate production workflow"
        )
    for arguments in (
        ("migrate", "--noinput"),
        ("collectstatic", "--noinput"),
        ("check",),
    ):
        result = subprocess.run(
            [sys.executable, "manage.py", *arguments], cwd=root, check=False
        )
        if result.returncode != 0:
            raise ValueError(
                "Local Django preparation failed; existing data is preserved"
            )
    verify_installed(root)
    return {
        "status": "ready",
        "version": manifest["version"],
        "us_offers": manifest["dataset"]["us_offers"],
        "road_points": manifest["road"]["points"],
        "credentials": "Set ORS_API_KEY in .env before starting",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle", type=Path, help="Use an already downloaded data ZIP"
    )
    args = parser.parse_args()
    try:
        print(json.dumps(setup(bundle=args.bundle), indent=2))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"Setup failed: {error}") from None


if __name__ == "__main__":
    main()
