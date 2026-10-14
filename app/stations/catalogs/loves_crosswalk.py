"""Shared loves crosswalk rules used by review and preparation tools."""

import re
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def integer_cell(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"non-numeric workbook ID: {value}") from error
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"non-integer workbook ID: {value}")
    return int(number)


def read_codes(workbook: Path) -> list[dict]:
    with zipfile.ZipFile(workbook) as archive:
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in item.iter(f"{NS}t"))
                for item in shared_root.findall(f"{NS}si")
            ]
        else:
            shared = []
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.findall(f"{NS}sheetData/{NS}row"):
        cells = {}
        for cell in row.findall(f"{NS}c"):
            column = re.match(r"[A-Z]+", cell.attrib["r"])
            if column is None:
                raise ValueError("workbook cell lacks column")
            value = cell.find(f"{NS}v")
            if cell.attrib.get("t") == "inlineStr":
                cells[column.group()] = "".join(
                    part.text or "" for part in cell.iter(f"{NS}t")
                )
            elif value is not None and value.text is not None:
                cells[column.group()] = (
                    shared[int(value.text)]
                    if cell.attrib.get("t") == "s"
                    else value.text
                )
        rows.append(cells)
    if not rows or [rows[0].get(column) for column in "ABCD"] != [
        "Store #",
        "City",
        "State",
        "OPIS",
    ]:
        raise ValueError("unexpected Love's station-code workbook columns")
    codes = []
    for row in rows[1:]:
        store_number = integer_cell(row.get("A"))
        if store_number is None:
            continue
        codes.append(
            {
                "store_number": store_number,
                "city": (row.get("B") or "").strip(),
                "state": (row.get("C") or "").strip().upper(),
                "opis_id": integer_cell(row.get("D")),
            }
        )
    return codes


def reconcile(queue: list[dict], codes: list[dict]) -> list[dict]:
    by_store: dict[int, list[dict]] = defaultdict(list)
    by_opis: dict[int, list[dict]] = defaultdict(list)
    for code in codes:
        by_store[code["store_number"]].append(code)
        if code["opis_id"] is not None:
            by_opis[code["opis_id"]].append(code)
    result = []
    for station in queue:
        branches = station["store_numbers"]
        operator_rows = by_store.get(branches[0], []) if len(branches) == 1 else []
        if len(branches) != 1:
            status = "ambiguous_source_branch"
        elif not operator_rows:
            status = (
                "operator_id_or_state_conflict"
                if by_opis.get(int(station["opis_id"]))
                else "operator_store_missing"
            )
        elif len(operator_rows) != 1:
            status = "ambiguous_operator_store"
        elif (
            operator_rows[0]["opis_id"] != int(station["opis_id"])
            or operator_rows[0]["state"] != station["state"]
        ):
            status = "operator_id_or_state_conflict"
        else:
            status = "exact_operator_id"
        result.append(
            {
                "opis_id": station["opis_id"],
                "store_numbers": branches,
                "source_city": station["city"],
                "source_state": station["state"],
                "status": status,
                "operator_matches": operator_rows
                or by_opis.get(int(station["opis_id"]), []),
                "coordinate_status": "pending_check",
                "access_status": "pending_check",
            }
        )
    return result
