"""실제 원본의 호기 재사용·명명 패턴·원본 전체 충돌·중복 근거를 함께 검증한다."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re
import sys

import pytest

DATABASE = Path(__file__).resolve().parents[1] / "database"
if str(DATABASE) not in sys.path:
    sys.path.insert(0, str(DATABASE))

from seed_augmentation import prepare_seed  # noqa: E402
from seed_equipment_names import assign_equipment_codes, original_equipment_conflicts  # noqa: E402


@pytest.fixture(scope="module")
def dataset():
    source = json.loads((DATABASE / "demo_release_catalog.json").read_text(encoding="utf-8"))
    effective, plan = prepare_seed(source)
    return effective, plan


def test_source_codes_are_reused_and_only_repeated_slots_get_pattern_names(dataset):
    source, before = dataset
    plan = deepcopy(before)
    records = assign_equipment_codes(source, plan)
    assert len(records) == len(plan) == len({entry["code"] for entry in plan}) == 300
    assert Counter(record["source_kind"] for record in records) == {"ORIGINAL_EXCEL": 278, "PATTERN_AUGMENTED": 22}
    assert Counter(record["model"] for record in records if record["source_kind"] == "PATTERN_AUGMENTED") == {
        "M-C3": 4, "M-C4 DFB": 4, "M-C4+": 2, "M-O5 Prime": 2, "HPA": 10}
    original_codes = {template["source_equipment"] for template in source["templates"]}
    for position, (old, entry, record) in enumerate(zip(before, plan, records, strict=True), 1):
        assert {key: value for key, value in entry.items() if key != "code"} == {key: value for key, value in old.items() if key != "code"}
        assert record["plan_index"] == position and entry["code"] == record["equipment_code"]
        assert re.fullmatch(r"ES[A-Z0-9]+", entry["code"])
        assert "DEMO" not in entry["code"]
        if record["source_kind"] == "ORIGINAL_EXCEL":
            assert entry["code"] == record["source_equipment"]
        else:
            assert entry["code"] not in original_codes
            prefix, suffix = re.fullmatch(r"(.+?)(\d+)", record["source_equipment"]).groups()
            assert re.fullmatch(re.escape(prefix) + r"\d{" + str(len(suffix)) + r"}", entry["code"])


def test_naming_is_reproducible_preserves_catalog_and_records_exact_original_cell(dataset):
    source, before = dataset
    snapshot = deepcopy(source)
    first_plan, second_plan = deepcopy(before), deepcopy(before)
    first = assign_equipment_codes(source, first_plan)
    second = assign_equipment_codes(source, second_plan)
    assert first == second and first_plan == second_plan and source == snapshot
    assert assign_equipment_codes(source, first_plan) == first
    for record in first:
        cells = source["workbooks"][record["workbook"]]["sheets"][record["sheet"]]
        original = str(cells[record["equipment_cell"]]).strip()
        assert original == record["source_equipment"] or original.startswith(record["source_equipment"] + " (")
        basis = record["pattern_basis"]
        assert basis["source_sheet"] == record["sheet"] and basis["source_cell"] == record["equipment_cell"]


def test_original_duplicate_is_documented_without_merging_its_locations(dataset):
    source, _ = dataset
    conflicts = original_equipment_conflicts(source)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["equipment_code"] == "ESPA02" and conflict["different_fields"] == ["line", "version"]
    assert {(location["sheet"], location["equipment_cell"], location["line"], location["version"])
            for location in conflict["locations"]} == {("P1", "E3", "P1F", "v3.52_22"), ("P2", "E3", "P2F", "v3.52_14")}


def test_original_mixed_pm_equipment_keeps_its_actual_source_identity(dataset):
    catalog, before = dataset
    plan = deepcopy(before)
    assignments = assign_equipment_codes(catalog, plan)
    mixed = [record for record in assignments if record["equipment_code"] == "ESON81"]
    assert len(mixed) == 1
    record = mixed[0]
    assert record["source_kind"] == "ORIGINAL_EXCEL" and record["source_equipment"] == "ESON81"
    assert (record["sheet"], record["equipment_cell"], record["source_site"], record["source_line"]) == (
        "Foundry", "D57", "화성", "NRD (MR2동)")
    assert catalog["workbooks"][record["workbook"]]["sheets"]["Foundry"]["D57"] == "ESON81"


def miniature(codes, *, raw_codes=()):
    cells = {f"A{index}": value for index, value in enumerate([*codes, *raw_codes], 1)}
    templates = [{"source_equipment": code, "workbook": 0, "sheet": "현황", "equipment_cell": f"A{index}",
                  "site": "평택", "line": "S5", "model": "HPA", "family": "hpa", "version": None}
                 for index, code in enumerate(codes, 1)]
    return {"templates": templates, "workbooks": [{"name": "원본.xlsx", "sheets": {"현황": cells}}]}


def test_generation_reserves_unselected_original_codes_and_excluded_raw_cells():
    catalog = miniature(["ESHC13", "ESHC14"], raw_codes=["ESHC15", "ESHC16 (JEP)"])
    plan = [{"template": 0, "code": "old"} for _ in range(3)]
    records = assign_equipment_codes(catalog, plan)
    assert [entry["code"] for entry in plan] == ["ESHC13", "ESHC17", "ESHC18"]
    assert [record["source_kind"] for record in records] == ["ORIGINAL_EXCEL", "PATTERN_AUGMENTED", "PATTERN_AUGMENTED"]


def test_numeric_width_is_preserved_when_numbering_wraps():
    catalog = miniature(["ESHC99"], raw_codes=["ESHC01"])
    plan = [{"template": 0, "code": "old"} for _ in range(2)]
    assign_equipment_codes(catalog, plan)
    assert [entry["code"] for entry in plan] == ["ESHC99", "ESHC02"]


def test_exhausted_pattern_fails_without_partially_renaming_plan():
    catalog = miniature(["ESA1"], raw_codes=[f"ESA{number}" for number in range(2, 10)])
    plan = [{"template": 0, "code": "old1"}, {"template": 0, "code": "old2"}]
    before = deepcopy(plan)
    with pytest.raises(ValueError, match="자릿수"):
        assign_equipment_codes(catalog, plan)
    assert plan == before
