"""Cross-check recorded operator coordinates against the live store JSON-LD."""

import json
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/review/imperial_ca_site_evidence.json"


class JsonLdScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self._inside = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self._inside = (
            tag == "script"
            and attributes.get("type", "").casefold() == "application/ld+json"
        )
        if self._inside:
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._inside:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._inside:
            self.scripts.append("".join(self._parts))
            self._inside = False


def matching_store(html: str, number: str) -> dict:
    parser = JsonLdScripts()
    parser.feed(html)
    for script in parser.scripts:
        try:
            document = json.loads(script)
        except ValueError:
            continue
        documents = document if isinstance(document, list) else [document]
        for item in documents:
            if isinstance(item, dict) and f"Store #{number}" in item.get("name", ""):
                return item
    raise ValueError(f"operator page has no JSON-LD store #{number}")


def main() -> None:
    records = json.loads(EVIDENCE.read_text(encoding="utf-8"))["records"]
    operator_records = [
        item for item in records if item["status"] == "operator_coordinate"
    ]
    for record in operator_records:
        response = httpx.get(record["evidence_url"], timeout=15, follow_redirects=True)
        response.raise_for_status()
        store = matching_store(response.text, record["store_number"])
        address = store["address"]
        geo = store["geo"]
        if (
            address["streetAddress"] != record["expected_address"]
            or address["addressLocality"] != "Westmorland"
            or address["addressRegion"] != "CA"
            or address["addressCountry"] != "US"
            or geo["latitude"] != record["latitude"]
            or geo["longitude"] != record["longitude"]
        ):
            raise ValueError("recorded operator location differs from live JSON-LD")
        print(f"Verified operator coordinate for OPIS {record['opis_id']}")


if __name__ == "__main__":
    main()
