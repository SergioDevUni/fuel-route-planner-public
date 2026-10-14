import zipfile

from scripts.build_loves_crosswalk import read_codes, reconcile


def test_crosswalk_preserves_exact_conflict_and_missing() -> None:
    queue = [
        {"opis_id": "135", "store_numbers": [766], "city": "Atkinson", "state": "IL"},
        {
            "opis_id": "1914",
            "store_numbers": [792],
            "city": "Green River",
            "state": "UT",
        },
        {
            "opis_id": "70793",
            "store_numbers": [119],
            "city": "Green River",
            "state": "UT",
        },
        {"opis_id": "289", "store_numbers": [173], "city": "Elsewhere", "state": "IL"},
    ]
    codes = [
        {"store_number": 766, "opis_id": 135, "city": "Atkinson", "state": "IL"},
        {"store_number": 792, "opis_id": 70793, "city": "Green River", "state": "UT"},
    ]
    result = reconcile(queue, codes)
    assert [item["status"] for item in result] == [
        "exact_operator_id",
        "operator_id_or_state_conflict",
        "operator_id_or_state_conflict",
        "operator_store_missing",
    ]
    assert result[2]["operator_matches"][0]["store_number"] == 792
    assert all(item["coordinate_status"] == "pending_check" for item in result)


def test_read_codes_handles_shared_strings_and_numeric_ids(tmp_path) -> None:
    workbook = tmp_path / "codes.xlsx"
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    strings = ["Store #", "City", "State", "OPIS", "Atkinson", " IL "]
    shared = "".join(f"<si><t>{value}</t></si>" for value in strings)
    sheet = """<sheetData>
      <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>
        <c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>
      <row r="2"><c r="A2"><v>766</v></c><c r="B2" t="s"><v>4</v></c>
        <c r="C2" t="s"><v>5</v></c><c r="D2"><v>135</v></c></row>
    </sheetData>"""
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml", f'<sst xmlns="{namespace}">{shared}</sst>'
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{namespace}">{sheet}</worksheet>',
        )
    assert read_codes(workbook) == [
        {"store_number": 766, "city": "Atkinson", "state": "IL", "opis_id": 135}
    ]
