"""데이터 관리 조회 도우미와 파일 이름 보관 형식 (DB 없음)."""

from setron.features.data_admin import WORKBOOK_KEY, sheet_names, with_workbook
from setron.features.data_admin.service import _model_mismatch, _workbook, sheet_cells
from setron.features.deletion.service import _sheet
from setron.features.import_save.service import batch_raw_data


def test_workbook_name_is_kept_under_a_key_that_cannot_be_a_sheet_name():
    raw = with_workbook({"Memory": {"A1": 1}}, " 마스터.xlsx ")
    assert raw == {"Memory": {"A1": 1}, WORKBOOK_KEY: "마스터.xlsx"}
    assert "[" in WORKBOOK_KEY and "]" in WORKBOOK_KEY  # 엑셀 시트 이름에는 [ ]를 쓸 수 없다
    assert with_workbook({"Memory": {}}, "") == {"Memory": {}} and with_workbook({"Memory": {}}, None) == {"Memory": {}}
    assert sheet_names(raw) == ("Memory",)


def test_import_batch_raw_data_stores_file_name_and_sheet_listing_ignores_it():
    raw = batch_raw_data({"sheet": "현황 2026-11-01", "cells": {"A1": "No"}, "merged": []}, "마스터.xlsx")
    assert raw == {"현황 2026-11-01": {"A1": "No"}, WORKBOOK_KEY: "마스터.xlsx"}
    assert batch_raw_data({"sheet": "S", "cells": {}, "merged": []}) == {"S": {}}
    assert _sheet(raw) == "현황 2026-11-01"  # 저장 자료 삭제 목록의 시트 이름에 파일 이름이 섞이지 않는다


def test_demo_seed_raw_data_keeps_sheets_under_a_sheets_key():
    # database/seed_source.py(demo_seed.v2)와 팀 Docker 시연 DB의 입력 작업 원본 형식 (11-12)
    raw = {"augmentation": "DEMO 호기와 6개월 관측은 합성", "seed": "demo_seed.v2:excel:300x6:2026-10-21",
           "sheets": {"P1": {"A1": "Line"}, "P3-S5": {"D5": "EP3S501"}}, "source_kind": "ORIGINAL_EXCEL",
           "source_workbook": "평택설비현황표_교육용_제작.xlsx", "synthetic": True}
    assert sheet_names(raw) == ("P1", "P3-S5")                       # 설명 키(augmentation · seed …)는 시트가 아니다
    assert sheet_cells(raw)["P3-S5"] == {"D5": "EP3S501"}
    assert _workbook(raw) == "평택설비현황표_교육용_제작.xlsx"
    imported = {"현황": {"A1": "No"}, WORKBOOK_KEY: "마스터.xlsx", "[현황표]": {"site": "평택"}}
    assert sheet_names(imported) == ("현황",) and _workbook(imported) == "마스터.xlsx"


def test_model_text_mismatch_uses_the_model_table_not_plain_text():
    assert not _model_mismatch("M-C4(DFB)", "M-C4 DFB") and not _model_mismatch("M-C3 (통합UI)", "M-C3")   # 엑셀 원문
    assert not _model_mismatch("5각 Cluster O2", "Cluster O2") and not _model_mismatch("C4H", "C4H")
    assert _model_mismatch("M-C4", "M-C3") and _model_mismatch("M-O2", "M-C4")                         # 다른 설비군
    assert not _model_mismatch("새 이름", "C111")   # 번역표가 모르는 이름은 판단하지 않는다

