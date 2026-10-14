"""Pin and prepare local US place and boundary assets from public snapshots.

Run explicitly; ordinary requests never download or geocode.
"""

import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

import shapefile
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "var" / "downloads"
OUTPUT = ROOT / "data" / "prepared" / "locations"
SOURCES = {
    "geonames_us": "https://download.geonames.org/export/dump/US.zip",
    "states_coarse": "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_20m.zip",
    "states_detailed": "https://www2.census.gov/geo/tiger/TIGER2023/STATE/tl_2023_us_state.zip",
}
US_STATES = frozenset(
    (
        "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
        "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA "
        "WV WI WY DC"
    ).split()
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(name: str, url: str) -> Path:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS / url.rsplit("/", 1)[-1]
    if target.exists():
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    request = Request(url, headers={"User-Agent": "fuel-route-planner-preparation/1.0"})
    with urlopen(request, timeout=45) as response, temporary.open("wb") as output:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            output.write(chunk)
    temporary.replace(target)
    print(f"Downloaded {name}: {target.stat().st_size} bytes")
    return target


def prepare_states(source: Path, target: Path) -> int:
    with ZipFile(source) as archive:
        members = {Path(name).suffix.lower(): name for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(members[".shp"])),
            shx=io.BytesIO(archive.read(members[".shx"])),
            dbf=io.BytesIO(archive.read(members[".dbf"])),
        )
        states = []
        for record in reader.iterShapeRecords():
            attributes = record.record.as_dict()
            code = attributes.get("STUSPS")
            if code not in US_STATES:
                continue
            polygon = shape(record.shape.__geo_interface__)
            if polygon.is_empty or not polygon.is_valid:
                raise ValueError(f"invalid state geometry: {code}")
            states.append({"code": code, "geometry": mapping(polygon)})
    if len(states) != 51:
        raise ValueError(f"expected 50 states plus DC; found {len(states)}")
    target.write_text(
        json.dumps(
            {"states": sorted(states, key=lambda state: state["code"])},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return len(states)


def prepare_places(source: Path, target: Path) -> int:
    count = 0
    with (
        ZipFile(source) as archive,
        archive.open("US.txt") as raw,
        target.open("w", encoding="utf-8", newline="\n") as output,
    ):
        stream = io.TextIOWrapper(raw, encoding="utf-8")
        for line in stream:
            columns = line.rstrip("\n").split("\t")
            if len(columns) < 19:
                raise ValueError("invalid GeoNames record")
            feature_class, feature_code = columns[6], columns[7]
            state = columns[10]
            if (
                feature_class != "P"
                or feature_code in {"PPLH", "PPLQ", "PPLW"}
                or state not in US_STATES
            ):
                continue
            aliases = list(
                dict.fromkeys(
                    name.strip()
                    for name in [columns[1], columns[2], *columns[3].split(",")]
                    if name.strip() and len(name.strip()) <= 200
                )
            )
            place = {
                "id": f"geonames:{columns[0]}",
                "name": columns[1],
                "aliases": aliases,
                "state": state,
                "latitude": float(columns[4]),
                "longitude": float(columns[5]),
                "population": int(columns[14] or 0),
            }
            output.write(
                json.dumps(place, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            count += 1
    if count < 10_000:
        raise ValueError(f"too few populated places: {count}")
    return count


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    inputs = {name: download(name, url) for name, url in SOURCES.items()}
    coarse = OUTPUT / "states_coarse.geojson"
    detailed = OUTPUT / "states_detailed.geojson"
    places = OUTPUT / "places.ndjson"
    counts = {
        "coarse_states": prepare_states(inputs["states_coarse"], coarse),
        "detailed_states": prepare_states(inputs["states_detailed"], detailed),
        "populated_places": prepare_places(inputs["geonames_us"], places),
    }
    manifest = {
        "sources": {
            name: {"url": SOURCES[name], "sha256": sha256(path)}
            for name, path in inputs.items()
        },
        "outputs": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in (coarse, detailed, places)
        },
        "counts": counts,
        "geo_names_license": "CC BY 4.0; attribution required",
        "census_boundary_vintage": "2023",
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
