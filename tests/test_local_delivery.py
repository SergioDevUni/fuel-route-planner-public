"""Small release fixtures exercise preservation and hostile archive boundaries."""

import hashlib
import json
import os
import sqlite3
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from scripts.local_common import (
    MANIFEST,
    asset_path,
    load_local_env,
    planner_hash,
    verify_installed,
)
from scripts.package_local_assets import package_assets
from scripts.setup_local import initialize_environment, install_bundle


@pytest.fixture
def release(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text("project = 'fixture'")
    (root / "uv.lock").write_text("version = 1")
    (root / ".env.example").write_text("DJANGO_SECRET_KEY=\nORS_API_KEY=\n")
    for folder in ("data/raw", "var/station_candidates", "var/road_nationwide"):
        (root / folder).mkdir(parents=True)
    csv = b"fixture source data"
    source_hash = hashlib.sha256(csv).hexdigest()
    (root / "data/raw/fuel.csv").write_bytes(csv)
    candidates = b'[{"station_id":"csv-1","price":"3.20"}]'
    (root / "var/station_candidates/candidates.json").write_bytes(candidates)
    (root / "var/station_candidates/manifest.json").write_text(
        json.dumps(
            {
                "source_sha256": source_hash,
                "candidate_sha256": hashlib.sha256(candidates).hexdigest(),
                "candidate_count": 1,
            }
        )
    )
    with sqlite3.connect(root / "var/app.sqlite3") as db:
        db.execute("CREATE TABLE app_tripresult (payload TEXT)")
        db.execute("INSERT INTO app_tripresult VALUES ('private trip canary')")
        db.execute(
            "CREATE TABLE app_datasetversion "
            "(source_sha256 TEXT, counts TEXT, status TEXT)"
        )
        db.execute(
            "INSERT INTO app_datasetversion VALUES (?, ?, 'active')",
            (source_hash, json.dumps({"station_ids": 1})),
        )
    with sqlite3.connect(root / "var/road_nationwide/station_edges_v2_live.db") as db:
        db.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        db.execute(
            "INSERT INTO metadata VALUES ('road_graph_version', 'fixture-road-v1')"
        )
        db.execute("CREATE TABLE point (point_id INTEGER)")
        db.execute("INSERT INTO point VALUES (7)")
        db.execute("CREATE TABLE complete_source (point_id INTEGER)")
        db.execute("INSERT INTO complete_source VALUES (7)")
    bundle = tmp_path / "assets.zip"
    descriptor = package_assets(root, bundle, "v-fixture")
    target = tmp_path / "target"
    target.mkdir()
    for name in ("pyproject.toml", "uv.lock", ".env.example"):
        (target / name).write_bytes((root / name).read_bytes())
    return root, target, bundle, descriptor


def test_packaging_preserves_live_results_but_seed_has_none(release):
    source, target, bundle, descriptor = release
    with sqlite3.connect(source / "var/app.sqlite3") as db:
        assert db.execute("SELECT * FROM app_tripresult").fetchall() == [
            ("private trip canary",)
        ]
    install_bundle(target, bundle, descriptor)
    manifest = verify_installed(target)
    assert manifest["road"]["points"] == 1
    assert manifest["road"]["completed_sources"] == 1
    with sqlite3.connect(target / "var/app.sqlite3") as db:
        assert db.execute("SELECT * FROM app_tripresult").fetchall() == []
    with ZipFile(bundle) as archive:
        assert ".env" not in archive.namelist()


def test_reinstall_preserves_existing_results_and_credentials(release):
    source, target, bundle, descriptor = release
    install_bundle(target, bundle, descriptor)
    initialize_environment(target)
    original_env = (target / ".env").read_bytes()
    with sqlite3.connect(target / "var/app.sqlite3") as db:
        db.execute("INSERT INTO app_tripresult VALUES ('new local result')")
    install_bundle(target, bundle, descriptor)
    initialize_environment(target)
    assert (target / ".env").read_bytes() == original_env
    with sqlite3.connect(target / "var/app.sqlite3") as db:
        assert db.execute("SELECT * FROM app_tripresult").fetchall() == [
            ("new local result",)
        ]
    verify_installed(target)


def test_wrong_bundle_or_code_is_rejected_before_installation(release):
    source, target, bundle, descriptor = release
    for change in ({"sha256": "0" * 64}, {"bytes": 1}, {"planner_sha256": "1" * 64}):
        with pytest.raises(ValueError):
            install_bundle(target, bundle, {**descriptor, **change})
        assert not (target / "var").exists()


def test_existing_changed_asset_prevents_install_without_replacement(release):
    source, target, bundle, descriptor = release
    path = target / "data/raw/fuel.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"user data")
    with pytest.raises(ValueError, match="preserved"):
        install_bundle(target, bundle, descriptor)
    assert path.read_bytes() == b"user data"
    assert not (target / "var").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/outside",
        "data/../outside",
        "data//x",
        "var/other/x",
        ".env",
        "data/C:/x",
        "data\\x",
    ],
)
def test_archive_paths_must_be_safe_and_allowed(tmp_path, name):
    with pytest.raises(ValueError):
        asset_path(tmp_path, name)


def test_existing_directory_link_cannot_send_assets_outside_root(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "data").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink privilege is unavailable")
    with pytest.raises(ValueError):
        asset_path(root, "data/escape")


@pytest.mark.parametrize("kind", ["duplicate", "link", "extra", "wrong_content"])
def test_malformed_archive_is_rejected_before_any_install(release, kind):
    source, target, bundle, descriptor = release
    with ZipFile(bundle) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    altered = bundle.with_name(kind + ".zip")
    with ZipFile(altered, "w", ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            if kind == "wrong_content" and name == "data/raw/fuel.csv":
                content = b"tampered"
            if kind == "link" and name == "data/raw/fuel.csv":
                info = ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, content)
            else:
                archive.writestr(name, content)
        if kind == "duplicate":
            with pytest.warns(UserWarning):
                archive.writestr(MANIFEST, contents[MANIFEST])
        if kind == "extra":
            archive.writestr(".env", "private")
    descriptor = {
        **descriptor,
        "bytes": altered.stat().st_size,
        "sha256": hashlib.sha256(altered.read_bytes()).hexdigest(),
    }
    with pytest.raises(ValueError):
        install_bundle(target, altered, descriptor)
    assert not (target / "var").exists()


def test_environment_values_are_literal_and_existing_environment_wins(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ORS_API_KEY", "environment-value")
    monkeypatch.delenv("LOCAL_TEST_LITERAL", raising=False)
    (tmp_path / ".env").write_text(
        "ORS_API_KEY=file-value\nLOCAL_TEST_LITERAL='$(do-not-run)'\n"
    )
    load_local_env(tmp_path)
    assert os.environ["ORS_API_KEY"] == "environment-value"
    assert os.environ["LOCAL_TEST_LITERAL"] == "$(do-not-run)"


@pytest.mark.parametrize(
    "line", ["bad assignment", "bad-key=value", "ORS_API_KEY='unclosed"]
)
def test_invalid_env_does_not_expose_values(tmp_path, line):
    (tmp_path / ".env").write_text(line)
    with pytest.raises(ValueError) as caught:
        load_local_env(tmp_path)
    assert line not in str(caught.value)


def test_mutated_installed_asset_is_detected(release):
    source, target, bundle, descriptor = release
    install_bundle(target, bundle, descriptor)
    (target / "data/raw/fuel.csv").write_text("modified")
    with pytest.raises(ValueError, match="differs"):
        verify_installed(target)


def test_planner_identity_survives_git_newline_normalization(tmp_path):
    hashes = []
    for ending, folder in ((b"\r\n", "windows"), (b"\n", "linux")):
        root = tmp_path / folder
        (root / "app").mkdir(parents=True)
        for name in ("pyproject.toml", "uv.lock", "app/planner.py"):
            (root / name).write_bytes(b"fixture" + ending)
        hashes.append(planner_hash(root))
    assert hashes[0] == hashes[1]
