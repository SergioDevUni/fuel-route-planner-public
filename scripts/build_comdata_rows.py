"""Extract a pinned public truckstop PDF into source-hashed review rows."""

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import httpx
import pymupdf

from app.stations.catalogs.paths import ROOT

SOURCE_URL = "https://www.comdata.com/GallonUpLocations"
PDF = ROOT / "var/operator_review/comdata_gallonup_2024.pdf"
OUTPUT = ROOT / "var/operator_review/comdata_rows.json"
COLUMNS = ((77, 167), (167, 316), (316, 457), (457, 482), (482, 560))


def extract_rows(path: Path) -> list[dict]:
    document = pymupdf.open(path)
    rows = []
    for page_number, page in enumerate(document, 1):
        lines: dict[float, list[tuple]] = defaultdict(list)
        for word in page.get_text("words"):
            lines[round(word[1], 1)].append(word)
        for line_number, (_, words) in enumerate(sorted(lines.items()), 1):
            words.sort(key=lambda word: word[0])
            state = [word[4] for word in words if word[0] < 70]
            if len(state) != 1 or not re.fullmatch(r"[A-Z]{2}", state[0]):
                continue
            city, name, address, postcode, code = (
                " ".join(word[4] for word in words if low <= word[0] < high)
                for low, high in COLUMNS
            )
            if (
                not city
                or not name
                or not address
                or not re.fullmatch(r"[A-Z]{2}\d{3}", code)
            ):
                raise ValueError(f"malformed public PDF row on page {page_number}")
            rows.append(
                {
                    "state": state[0],
                    "city": city,
                    "name": name,
                    "address": address,
                    "postcode": postcode,
                    "location_code": code,
                    "page": page_number,
                    "line": line_number,
                }
            )
    if len(document) != 50 or len(rows) != 3116:
        raise ValueError("public truckstop PDF page or row count changed")
    return rows


def build(fetch: bool = False) -> dict:
    if fetch:
        response = httpx.get(SOURCE_URL, timeout=30, follow_redirects=True)
        response.raise_for_status()
        temporary = PDF.with_suffix(".pdf.tmp")
        temporary.write_bytes(response.content)
        os.replace(temporary, PDF)
    pdf_bytes = PDF.read_bytes()
    rows = extract_rows(PDF)
    result = {
        "source_url": SOURCE_URL,
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "pdf_page_count": 50,
        "row_count": len(rows),
        "invalid_postcode_count": sum(
            not re.fullmatch(r"\d{5}", row["postcode"]) for row in rows
        ),
        "rows": rows,
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, OUTPUT)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    arguments = parser.parse_args()
    result = build(arguments.fetch)
    print(result["row_count"], "public truckstop rows extracted")


if __name__ == "__main__":
    main()
