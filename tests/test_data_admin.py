"""데이터 관리 조회 도우미와 파일 이름 보관 형식 (DB 없음)."""

from setron.features.data_admin import WORKBOOK_KEY, sheet_names, with_workbook
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
