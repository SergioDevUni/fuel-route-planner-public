"""Package current source with the pinned data bundle for local delivery."""

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from scripts.local_common import ROOT, planner_hash, sha256

SOURCE_DIRS = (".github", "app", "config", "docs", "scripts", "tests")
SOURCE_FILES = (
    ".env.example",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    "Dockerfile",
    "compose.yaml",
    "compose.production.yaml",
    "README.md",
    "manage.py",
    "pyproject.toml",
    "uv.lock",
    "local-release.json",
)

PRESENTATION_DOCS = {
    "fuel-route-planner.postman_collection.json",
    "openapi.yaml",
    "images/multiple-stops.png",
    "images/fuel-stop-detail.png",
}


def package(root: Path, output: Path, *, bundle: Path | None = None) -> dict:
    descriptor = json.loads((root / "local-release.json").read_text())
    asset = bundle or root / "var/delivery" / descriptor["asset"]
    if (
        asset.stat().st_size != descriptor["bytes"]
        or sha256(asset) != descriptor["sha256"]
        or planner_hash(root) != descriptor["planner_sha256"]
    ):
        raise ValueError("Project and data must match the pinned release")
    if output.exists():
        raise ValueError("Use a new release directory")
    version = descriptor["version"]
    content = {}
    for folder in SOURCE_DIRS:
        for path in sorted((root / folder).rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
                and (
                    folder != "docs"
                    or path.relative_to(root / "docs").as_posix() in PRESENTATION_DOCS
                )
            ):
                content[path.relative_to(root).as_posix()] = path.read_bytes()
    for name in SOURCE_FILES:
        content[name] = (root / name).read_bytes()
    with ZipFile(asset) as archive:
        for name in archive.namelist():
            if name.startswith(("data/raw/", "data/review/")):
                content[name] = archive.read(name)
    records = {
        name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        for name, data in sorted(content.items())
    }
    asset_name = "var/container-assets/assets.zip"
    content["var/container-assets/local-release.json"] = content["local-release.json"]
    records["var/container-assets/local-release.json"] = records["local-release.json"]
    records[asset_name] = {
        "sha256": descriptor["sha256"],
        "bytes": descriptor["bytes"],
    }
    manifest = {
        "schema": 1,
        "version": version,
        "created_utc": datetime.now(UTC).isoformat(),
        "planner_sha256": descriptor["planner_sha256"],
        "road": descriptor["road"],
        "contents": records,
    }
    output.mkdir(parents=True)
    shutil.copyfile(asset, output / descriptor["asset"])
    shutil.copyfile(root / "local-release.json", output / "local-release.json")
    project = output / f"fuel-route-planner-{version}.zip"
    prefix = f"fuel-route-planner-{version}/"
    with ZipFile(project, "x", ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(content.items()):
            archive.writestr(prefix + name, data)
        archive.write(asset, prefix + asset_name, compress_type=ZIP_STORED)
        archive.writestr(
            prefix + "RELEASE_MANIFEST.json", json.dumps(manifest, indent=2) + "\n"
        )
    with ZipFile(project) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive integrity check did not pass")
    for path in (project, output / descriptor["asset"]):
        path.with_suffix(path.suffix + ".sha256").write_text(
            f"{sha256(path)}  {path.name}\n", encoding="ascii"
        )
    (output / "SHA256SUMS.txt").write_text(
        "".join(
            f"{sha256(path)}  {path.name}\n"
            for path in sorted(output.iterdir())
            if path.is_file() and not path.name.endswith(".sha256")
        ),
        encoding="ascii",
    )
    return {
        "release_directory": str(output),
        "version": version,
        "project_archive_bytes": project.stat().st_size,
        "project_files": len(records),
        "road": descriptor["road"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bundle", type=Path, help="Use this pinned asset ZIP")
    args = parser.parse_args()
    descriptor = json.loads((ROOT / "local-release.json").read_text())
    output = args.output or ROOT / "var/delivery" / descriptor["version"]
    print(json.dumps(package(ROOT, output, bundle=args.bundle), indent=2))


if __name__ == "__main__":
    main()
