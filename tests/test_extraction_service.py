"""BE-007: 업무 Excel 배치를 본뜬 합성 셀과 모의 AI 응답 검증.

vTEST 버전과 TEST 호기는 시험값이다. 실제 설비·릴리즈 대응이나 회사 AI 정확도를 뜻하지 않는다.
"""

from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace

import pytest

from setron.features.ai.schemas import AIStatus, ExtractKind
from setron.features.extraction.service import extract_sheet_data
from setron.infrastructure.ai_client import (
    AIConnectionError, AIRateLimitError, AITimeoutError, ExampleChatClient, ExampleFailure, ResponseSource,
)
from setron.infrastructure.excel_reader import MergedRange, SheetData


EQUIPMENT = ExtractKind.EQUIPMENT_STATUS
RELEASE = ExtractKind.RELEASE_NOTE


def reply(*rows, notes=()):
    return json.dumps({"rows": [{"values": values, "sources": sources} for values, sources in rows],
                       "review_notes": list(notes)}, ensure_ascii=False)


def extract(sheet, *rows, kind=EQUIPMENT):
    client = ExampleChatClient([reply(*rows)])
    result = extract_sheet_data(client, kind, sheet)
    assert result.ok
    return result.data, client


def equipment_sheet(**changes):
    cells = [["기준일", datetime(2026, 10, 27), "사업장", "시험사업장"],
             ["설비군", "모델", "호기", "Version", "Engine", "EPD", "라인"],
             ["Oxide", "M5", "TEST-001", "vTEST.2_n4 ", "engine-test", "epd-test", "TEST-L1"]]
    options = dict(workbook_name="합성_현황.xlsx", sheet_name="시험현황", first_row=3, first_col=2, values=cells)
    options.update(changes)
    return SheetData(**options)


def equipment_row(**changes):
    values = {"base_date": "2026-10-27", "site": "시험사업장", "group_name": "Oxide", "model_name": "M5",
              "equipment_code": "TEST-001", "version_raw": "vTEST.2_n4", "engine_version": "engine-test",
              "epd_version": "epd-test", "line": "TEST-L1"}
    sources = {"base_date": "C3", "site": "E3", "group_name": "B5", "model_name": "C5",
               "equipment_code": "D5", "version_raw": "E5", "engine_version": "F5", "epd_version": "G5", "line": "H5"}
    values.update(changes)
    return values, sources


def test_sheetdata_adapter_keeps_offsets_dates_originals_and_components():
    sheet = equipment_sheet()
    original = deepcopy(sheet)
    data, client = extract(sheet, equipment_row())
    row = data.rows[0]
    assert sheet == original
    assert data.workbook_name == "합성_현황.xlsx"
    assert row.values["model_name"] == "M-O5" and row.raw_values["model_name"] == "M5"
    assert row.values["base_date"] == "2026-10-27"
    assert row.values["production_area"] is None
    assert not any(note.field == "production_area" for note in data.issues)
    assert row.values["version_raw"] == "vTEST.2_n4 "
    assert row.values["engine_version"] == "engine-test" and row.values["epd_version"] == "epd-test"
    assert row.sources["version_raw"].value == "vTEST.2_n4 "
    assert row.source_reference["equipment_code"] == {"sheet": "시험현황", "cells": ["D5"]}
    assert data.raw_data["시험현황"]["C3"] == "2026-10-27 00:00:00"
    assert "workbook_name" not in data.raw_data
    assert "E5: vTEST.2_n4 " in client.calls[0][1].content


@pytest.mark.parametrize("layout", ["vertical", "horizontal", "parallel"])
def test_different_layouts_keep_each_pm_and_source(layout):
    # 평택 자료의 병렬 표와 PM별 모델 표기, 릴리즈의 가로·세로 배치를 본뜬 합성 자료.
    if layout == "vertical":
        values = [["PM", "모델", "Version"], ["PM1", "M-O2", "vTEST.O2"], ["PM2", "M-O3", "vTEST.O3"]]
        positions = [("A2", "B2", "C2"), ("A3", "B3", "C3")]
    elif layout == "horizontal":
        values = [["PM", "PM1", "PM2"], ["모델", "M-O2", "M-O3"], ["Version", "vTEST.O2", "vTEST.O3"]]
        positions = [("B1", "B2", "B3"), ("C1", "C2", "C3")]
    else:
        values = [["PM", "모델", "Version", None, "PM", "모델", "Version"],
                  ["PM1", "M-O2", "vTEST.O2", None, "PM2", "M-O3", "vTEST.O3"]]
        positions = [("A2", "B2", "C2"), ("E2", "F2", "G2")]
    sheet = SheetData("혼합형_합성.xlsx", "혼합", 1, 1, values)
    rows = [({"pm_name": f"PM{i}", "model_name": model, "version_raw": version},
             dict(zip(("pm_name", "model_name", "version_raw"), addresses)))
            for i, model, version, addresses in ((1, "M-O2", "vTEST.O2", positions[0]), (2, "M-O3", "vTEST.O3", positions[1]))]
    data, _ = extract(sheet, *rows)
    assert [(row.values["pm_name"], row.values["model_name"], row.values["version_raw"]) for row in data.rows] == [
        ("PM1", "M-O2", "vTEST.O2"), ("PM2", "M-O3", "vTEST.O3")]
    assert data.rows[1].sources["version_raw"].address == positions[1][2]


def test_merged_source_resolves_to_anchor_and_prompt_contains_merge_metadata():
    sheet = SheetData("합성.xlsx", "병합", 4, 3, [["시험사업장", None], [False, 0]], [MergedRange(4, 3, 4, 4)])
    data, client = extract(sheet, ({"site": "시험사업장"}, {"site": "$d$4"}))
    assert data.rows[0].sources["site"].address == "C4"
    assert "병합 범위: C4:D4" in client.calls[0][1].content
    assert sheet.values[0][1] is None
    assert data.raw_data["병합"]["C5"] is False
    assert data.raw_data["병합"]["D5"] == 0


def mixed_sheet():
    """실제 호출에서 확인한 혼합형 배치. 호기·버전은 합성 시험값이다."""
    return SheetData("혼합형_합성.xlsx", "Foundry", 1, 1,
        [["모델", "호기", "SW", "PM 구성"],
         ["M-O2/O3", "TEST-MIX", "O3: vTEST.3_n4 / O2: vTEST.2_F2", "PM1, 2는 O3, PM3, 4는 O2"]])


def mixed_row(pm, model, version):
    return ({"model_name": model, "equipment_code": "TEST-MIX", "version_raw": version, "pm_name": pm},
            {"model_name": "A2", "equipment_code": "B2", "version_raw": "C2", "pm_name": "D2"})


def test_compound_sw_cell_keeps_model_pm_version_links_and_original_text():
    data, _ = extract(mixed_sheet(),
        mixed_row("PM1", "O3", "vTEST.3_n4"), mixed_row("2", "O3", "vTEST.3_n4"),
        mixed_row("PM3", "M-O2", "vTEST.2_F2"), mixed_row("4", "M-O2", "vTEST.2_F2"))
    assert [(row.values["pm_name"], row.values["model_name"], row.values["version_raw"]) for row in data.rows] == [
        ("PM1", "M-O3", "vTEST.3_n4"), ("PM2", "M-O3", "vTEST.3_n4"),
        ("PM3", "M-O2", "vTEST.2_F2"), ("PM4", "M-O2", "vTEST.2_F2")]
    assert data.rows[1].raw_values["pm_name"] == "2" and data.rows[1].raw_values["model_name"] == "O3"
    assert all(row.sources["version_raw"].value == "O3: vTEST.3_n4 / O2: vTEST.2_F2" for row in data.rows)
    assert all(row.source_reference["version_raw"] == {"sheet": "Foundry", "cells": ["C2"]} for row in data.rows)
    assert not any("원본 셀에서 확인" in issue.reason for issue in data.issues)


@pytest.mark.parametrize("model,version", [("O3", "vTEST.3"), ("O3", "vTEST.2_F2"), (None, "vTEST.3_n4")])
def test_compound_sw_rejects_shortened_wrong_family_and_unresolved_values(model, version):
    values, sources = mixed_row("PM1", "O3", version)
    values["model_name"] = model
    data, _ = extract(mixed_sheet(), (values, sources))
    assert data.rows[0].values["version_raw"] is None


@pytest.mark.parametrize("text", ["O3: vTEST.3_n4 / O3: vTEST.3_n4", "SW: vTEST.2 / Engine: vTEST.3_n4"])
def test_compound_sw_does_not_accept_ambiguous_labels_or_other_components(text):
    sheet = mixed_sheet()
    sheet.values[1][2] = text
    data, _ = extract(sheet, mixed_row("PM1", "O3", "vTEST.3_n4"))
    assert data.rows[0].values["version_raw"] is None


def test_pm_number_is_not_taken_from_part_of_a_larger_position():
    sheet = mixed_sheet()
    sheet.values[1][3] = "PM12는 O3"
    data, _ = extract(sheet, mixed_row("PM1", "O3", "vTEST.3_n4"))
    assert data.rows[0].values["pm_name"] is None
    assert any(issue.field == "pm_name" for issue in data.issues)


def test_short_model_name_without_shared_prefix_evidence_is_not_expanded():
    sheet = mixed_sheet()
    sheet.values[1][0] = "O3"
    data, _ = extract(sheet, mixed_row("PM1", "O3", "vTEST.3_n4"))
    assert data.rows[0].values["model_name"] == "O3"


@pytest.mark.parametrize("address", ["Z99", "다른시트!E5", "E5:E6", "A0", None])
def test_missing_or_invalid_source_never_becomes_confirmed_value(address):
    values, sources = equipment_row()
    sources["version_raw"] = address
    if address is None:
        sources.pop("version_raw")
    data, _ = extract(equipment_sheet(), (values, sources))
    assert data.rows[0].values["version_raw"] is None
    assert data.needs_review
    assert any(note.field == "version_raw" and note.row_number == 1 for note in data.issues)


@pytest.mark.parametrize("field,value", [("version_raw", "vTEST.2"), ("version_raw", "vINVENTED"), ("equipment_code", "TEST")])
def test_invented_or_shortened_identifiers_are_retained_only_as_unconfirmed_raw(field, value):
    data, _ = extract(equipment_sheet(), equipment_row(**{field: value}))
    assert data.rows[0].values[field] is None
    assert data.rows[0].raw_values[field] == value
    assert data.rows[0].sources[field].value is not None


@pytest.mark.parametrize("column,value,header", [("F5", "engine-test", "Engine"), ("G5", "epd-test", "EPD"),
                                               ("F5", "engine-test", "Engine Ver."), ("G5", "epd-test", "EPD Version")])
def test_engine_or_epd_cannot_replace_sw_even_if_ai_mislabels_it(column, value, header):
    values, sources = equipment_row(version_raw=value)
    sources["version_raw"] = column
    sheet = equipment_sheet()
    sheet.values[1][{"F5": 4, "G5": 5}[column]] = header
    data, _ = extract(sheet, (values, sources))
    assert data.rows[0].values["version_raw"] is None
    assert any("SW 버전으로" in note.reason for note in data.issues)


def family_row(model, family, category, ui=None, geometry=None, area=None, structure=None):
    values = {"model_name": model, "family_name": family, "group_name": category,
              "ui_type": ui, "geometry_type": geometry, "production_area": area, "structure_type": structure}
    sheet = SheetData("계열_합성.xlsx", "계열", 1, 1, [list(values), list(values.values())])
    sources = {field: sheet.address_of(1, i) for i, field in enumerate(values) if values[field] is not None}
    data, _ = extract(sheet, (values, sources))
    return data.rows[0]


def test_c3_keys_ignore_geometry_and_area_but_separate_ui():
    first = family_row("M-C3", "C3", "Poly", "통합UI", "5각", "Memory")
    second = family_row("M-C3", "C3", "Poly", "통합UI", "6각", "Foundry")
    legacy = family_row("M-C3", "C3", "Poly", "구UI", "6각")
    unknown = family_row("M-C3", "C3", "Poly", geometry="5각")
    assert first.family_key == second.family_key
    assert first.family_key != legacy.family_key
    assert unknown.family_key is None
    assert first.values["geometry_type"] == "5각" and second.raw_values["geometry_type"] == "6각"


def test_kctc_inline_cluster_and_oxide_o2_are_separate():
    inline = family_row("Inline O2", "O2", "KCTC")
    cluster = family_row("Cluster O2", "O2", "KCTC")
    oxide = family_row("M-O2", "O2", "Oxide")
    assert len({inline.family_key, cluster.family_key, oxide.family_key}) == 3
    conflict = family_row("M-O2", "O2", "KCTC")
    assert conflict.family_key is None
    unknown_structure = family_row("미확인 모델", "O2", "KCTC", structure="알 수 없음")
    assert unknown_structure.family_key is None


def test_prime_is_not_collapsed_into_original_and_c2_is_review_only():
    prime = family_row("M-O5 Prime", "O5", "Oxide")
    original = family_row("M5", "O5", "Oxide")
    c2 = family_row("M-C2", "C2", "Poly")
    assert prime.values["model_name"] == "M-O5 Prime"
    assert prime.family_key != original.family_key
    assert c2.values["model_name"] is None and c2.raw_values["model_name"] == "M-C2"
    assert c2.family_key is None
    assert any("C2는" in issue.reason for issue in c2.issues)


def test_prime_cannot_be_shortened_to_original_by_ai():
    sheet = SheetData("합성.xlsx", "모델", 1, 1, [["M-O5 Prime"]])
    data, _ = extract(sheet, ({"model_name": "M-O5"}, {"model_name": "A1"}))
    assert data.rows[0].values["model_name"] is None
    assert data.rows[0].sources["model_name"].value == "M-O5 Prime"


def test_hpa_is_preserved_with_missing_required_fields_for_review():
    sheet = SheetData("합성.xlsx", "HPA", 1, 1, [["HPA", "vTEST.HPA"], ["HPA", None]])
    data, _ = extract(sheet,
        ({"model_name": "HPA", "version_raw": "vTEST.HPA"}, {"model_name": "A1", "version_raw": "B1"}),
        ({"model_name": "HPA"}, {"model_name": "A2"}))
    assert len(data.rows) == 2 and all(row.values["model_name"] == "HPA" for row in data.rows)
    assert all(row.values["equipment_code"] is None for row in data.rows)
    assert any(note.row_number == 2 and note.field == "version_raw" for note in data.issues)


def test_release_version_date_items_and_modification_mark_keep_link_and_provenance():
    sheet = SheetData("릴리즈_합성.xlsx", "구UI", 1, 1,
                      [["Poly", "M-C3", "구UI", "C3"], ["Version", "No", "내용", "개조형", "표식"],
                       ["vTEST.1_n4 (2026-10-27)", 1, "시험 Item A", "M-O5 Prime", "○"],
                       [None, 2, "시험 Item B", None, None]], [MergedRange(3, 1, 4, 1)])
    common = {"version_raw": "vTEST.1_n4", "release_date": "2026-10-27", "group_name": "Poly", "model_name": "M-C3", "ui_type": "구UI", "family_name": "C3"}
    common_sources = {"version_raw": "A3", "release_date": "A3", "group_name": "A1", "model_name": "B1", "ui_type": "C1", "family_name": "D1"}
    data, _ = extract(sheet,
        (dict(common, item_no="1", content="시험 Item A", target_models="M-O5 Prime", modification_mark="○"),
         dict(common_sources, item_no="B3", content="C3", target_models="D3", modification_mark="E3")),
        (dict(common, item_no="2", content="시험 Item B"), dict(common_sources, item_no="B4", content="C4")), kind=RELEASE)
    assert len(data.rows) == 2
    assert data.rows[0].values["modification_mark"] == "○"
    assert data.rows[0].values["target_models"] == "M-O5 Prime"
    assert data.rows[1].sources["version_raw"].address == "A3"
    assert data.rows[0].family_key == data.rows[1].family_key
    assert not data.needs_review


def test_two_c3_sheets_remain_separate_results():
    keys = []
    for ui in ("구UI", "통합UI"):
        sheet = SheetData("같은파일.xlsx", ui, 1, 1, [["Poly", "M-C3", "C3", ui, "vTEST", "시험 Item"]])
        values = dict(zip(("group_name", "model_name", "family_name", "ui_type", "version_raw", "content"), sheet.values[0]))
        sources = {field: sheet.address_of(0, i) for i, field in enumerate(values)}
        data, _ = extract(sheet, (values, sources), kind=RELEASE)
        assert data.sheet_name == ui and list(data.raw_data) == [ui]
        keys.append(data.rows[0].family_key)
    assert keys[0] != keys[1]


def test_horizontal_release_keeps_version_item_pairs_and_combined_date_sources():
    sheet = SheetData("릴리즈_가로_합성.xlsx", "Memory", 1, 1,
                      [["Version", "vTEST.1 (2026.10.01)", "vTEST.2_n4 (2026.10.02)"],
                       ["No", 1, 2], ["내용", "시험 Item A", "시험 Item B"]])
    rows = [({"version_raw": version, "release_date": released, "item_no": str(i), "content": content},
             {"version_raw": f"{col}1", "release_date": f"{col}1", "item_no": f"{col}2", "content": f"{col}3"})
            for i, col, version, released, content in ((1, "B", "vTEST.1", "2026.10.01", "시험 Item A"),
                (2, "C", "vTEST.2_n4", "2026.10.02", "시험 Item B"))]
    data, _ = extract(sheet, *rows, kind=RELEASE)
    assert [(row.values["version_raw"], row.values["content"], row.values["release_date"]) for row in data.rows] == [
        ("vTEST.1", "시험 Item A", "2026-10-01"), ("vTEST.2_n4", "시험 Item B", "2026-10-02")]
    assert data.rows[1].source_reference["version_raw"]["cells"] == ["C1"]


def test_missing_fields_bad_date_and_unexplained_modification_have_locations():
    sheet = SheetData("합성.xlsx", "릴리즈", 1, 1, [["vTEST", "2026-02-30", "○"]])
    data, _ = extract(sheet, ({"version_raw": "vTEST", "release_date": "2026-02-30", "modification_mark": "○"},
                             {"version_raw": "A1", "release_date": "B1", "modification_mark": "C1"}), kind=RELEASE)
    assert data.rows[0].values["release_date"] is None
    assert any(note.field == "release_date" and note.address == "B1" for note in data.issues)
    assert any(note.field == "modification_mark" and note.address == "C1" for note in data.issues)
    assert any(note.field == "content" for note in data.issues)


def test_excel_error_is_not_a_version():
    sheet = equipment_sheet()
    sheet.values[2][3] = "#N/A"
    data, _ = extract(sheet, equipment_row(version_raw="#N/A"))
    assert data.rows[0].values["version_raw"] is None
    assert any("Excel 오류" in note.reason for note in data.issues)


@pytest.mark.parametrize("failure,status", [(AIConnectionError("x"), AIStatus.FAILED), (AITimeoutError("x"), AIStatus.FAILED), (AIRateLimitError("x"), AIStatus.RATE_LIMITED)])
def test_transport_failure_never_returns_partial_review_or_retries(failure, status):
    client = ExampleChatClient([ExampleFailure(failure)])
    result = extract_sheet_data(client, EQUIPMENT, equipment_sheet())
    assert result.status is status and result.data is None and len(client.calls) == 1


@pytest.mark.parametrize("payload", ["not json", '{"rows":{}}', '{"rows":[null]}', '{"rows":[{"values":{"version_raw":{"nested":"bad"}}}]}', '{"rows":[{"sources":{"unknown":"A1"}}]}'])
def test_bad_ai_response_is_invalid(payload):
    result = extract_sheet_data(ExampleChatClient([payload]), EQUIPMENT, equipment_sheet())
    assert result.status is AIStatus.INVALID_RESPONSE and result.data is None


def test_empty_sheet_does_not_call_ai_and_empty_result_requires_review():
    client = ExampleChatClient([])
    result = extract_sheet_data(client, EQUIPMENT, SheetData("빈.xlsx", "빈", 1, 1, [[None, ""]]))
    assert result.ok and result.data.needs_review and client.calls == []
    data, _ = extract(equipment_sheet())
    assert data.needs_review and data.rows == ()


def test_ai_notes_and_response_source_are_preserved():
    client = ExampleChatClient([reply(equipment_row(), notes=[{"reason": "충돌 확인", "field": "site", "address": "E3"}])])
    result = extract_sheet_data(client, EQUIPMENT, equipment_sheet())
    assert result.source is ResponseSource.EXAMPLE
    assert result.raw_content and result.data.issues[0].reason == "충돌 확인"


def test_review_originals_are_readable_by_existing_release_repository():
    from setron.features.release.repository import ReleaseRepository

    sheet = SheetData("합성.xlsx", "원본", 1, 1, [["vTEST", "시험 Item"]])
    data, _ = extract(sheet, ({"version_raw": "vTEST", "content": "시험 Item"},
                             {"version_raw": "A1", "content": "B1"}), kind=RELEASE)
    batch = {"raw_data": data.raw_data, "source_type": "EXCEL", "imported_at": datetime(2026, 10, 27)}
    tx = SimpleNamespace(get=lambda *args: batch)
    current = {"batch_id": 1, "source_reference": data.rows[0].source_reference}
    view = ReleaseRepository().view(tx, "release_version", current)
    assert [(source.address, source.value) for source in view.original_cells] == [("A1", "vTEST"), ("B1", "시험 Item")]


def test_editing_sheet_during_ai_call_does_not_change_request_snapshot():
    sheet = equipment_sheet()

    class EditingClient(ExampleChatClient):
        def complete(self, messages):
            sheet.values[2][3] = "changed after request"
            return super().complete(messages)

    result = extract_sheet_data(EditingClient([reply(equipment_row())]), EQUIPMENT, sheet)
    assert result.data.rows[0].values["version_raw"] == "vTEST.2_n4 "
    assert result.data.raw_data["시험현황"]["E5"] == "vTEST.2_n4 "
