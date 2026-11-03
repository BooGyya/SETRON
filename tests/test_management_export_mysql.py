"""관리 데이터 전체 Excel 내보내기를 실제 MySQL 5.6.19에서 검증한다 (BE-021, REQ-023·REQ-063)."""

import os
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from setron.features.ai.schemas import ExtractKind
from setron.features.equipment.repository import EquipmentRepository
from setron.features.export.management import SHEETS, build_management_workbook, save_management_data
from setron.features.import_save.service import DatabaseImportSaver
from setron.infrastructure.database import Database
from setron.features.storage.service import StorageService
from test_import_save_mysql import EQUIPMENT, request, row, storage  # noqa: F401  시드 적용 fixture 재사용
from test_schema_mysql import schema_database  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")

FORMULA = "=1+1 수식처럼 보이는 내용"


@pytest.fixture(scope="module")
def book(storage):  # noqa: F811
    saver = DatabaseImportSaver(storage)
    saver.save(request(ExtractKind.RELEASE_NOTE, [
        row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v1", content=FORMULA, item_no=7,
            release_date="2026-09-01", model_name="M-O2", family_name="O2")], "mgmt-release"))
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [
        row(1, {"equipment_code": "C5", "version_raw": "F5", "pm_name": "E5"},
            **{**EQUIPMENT, "equipment_code": "MG-1", "model_name": "M-O2", "family_name": "O2",
               "version_raw": "v1", "pm_name": "PM1"}),
        row(2, {"equipment_code": "C5", "version_raw": "F6", "pm_name": "E6"},
            **{**EQUIPMENT, "equipment_code": "MG-1", "model_name": "M-O3", "family_name": "O3",
               "version_raw": "v3", "pm_name": "PM3"}),
    ], "mgmt-equipment"))
    with storage.transaction() as tx:
        o2 = [f for f in _rows(tx, "version_family") if f["family_name"] == "O2"][0]
        v1 = [r for r in _rows(tx, "release_version", family_id=o2["family_id"]) if r["version_raw"] == "v1"][0]
        tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": o2["family_id"], "risk_reason": "가상 결함",
                                "release_version_id": v1["release_version_id"], "target_reference": {"family": "O2"},
                                "mapping_status": "VERIFIED"})
    return build_management_workbook(storage, now=lambda: datetime(2026, 11, 3, 12, 0))


def _rows(tx, table, **filters):
    return EquipmentRepository().rows(tx, table, **filters)


def values(sheet):
    return [list(r) for r in sheet.iter_rows(values_only=True)]


def test_sheets_follow_data_kinds(book):
    assert book.sheetnames == ["안내", *(title for title, _ in SHEETS)]
    assert values(book["안내"])[1] == ["생성 시각", "2026-11-03 12:00:00"]


def test_catalog_lists_models_with_families(book):
    rows = values(book["모델·계열"])
    assert ["Oxide", "M-O5 Prime", "M-O5", "예", "", ""] in rows
    assert ["HPA", "HPA", "", "아니오 (분석 제외)", "HPA", ""] in rows
    assert ["Poly", "M-C3", "", "예", "C3 통합UI", "INTEGRATED"] in rows


def test_equipment_has_one_row_per_pm_and_software_with_names(book):
    rows = [r for r in values(book["설비 현황"])[1:] if r[0] == "MG-1"]
    assert [(r[10], r[11], r[13], r[14], r[15]) for r in rows] == [
        ("PM1", "M-O2", "O2", "v1", "v1"), ("PM3", "M-O3", "O3", "v3", "미등록")]
    assert rows[0][5] == "혼합·미확인" and rows[0][2] == "화성"


def test_release_item_and_risk_use_readable_names(book):
    release = [r for r in values(book["릴리즈"])[1:] if r[1] == "O2"][0]
    assert release[:7] == ["M-O2", "O2", "v1", "", date(2026, 9, 1), "미확인", "미확인"]
    item = [r for r in values(book["Item"])[1:] if r[3] == FORMULA][0]
    assert item[:7] == ["O2", "v1", "7", FORMULA, "적용", "미확인", ""]
    risk = values(book["위험 기준"])[1]
    assert risk[:5] == ["직접 지정 버전", "O2", "계열 전체", "", "v1"] and risk[-2:] == ["사용", "확인됨"]
    assert values(book["관리 설정"])[1][0] == "version_age_thresholds"


def test_saved_file_keeps_formula_like_text_as_text(storage, book, tmp_path):  # noqa: F811
    result = save_management_data(storage, tmp_path / "관리데이터.xlsx")

    assert result.saved and "설비 현황" in result.message
    saved = load_workbook(tmp_path / "관리데이터.xlsx")
    cell = next(c for row in saved["Item"].iter_rows() for c in row if c.value == FORMULA)
    assert cell.data_type == "s"


def test_wrong_name_and_db_failure_are_reported(storage, tmp_path):  # noqa: F811
    assert save_management_data(storage, tmp_path / "x.csv").message == "파일 이름은 .xlsx로 끝나야 합니다."
    broken = Database.from_env({"SETRON_DB_HOST": "127.0.0.1", "SETRON_DB_PORT": "1", "SETRON_DB_NAME": "x",
                                "SETRON_DB_USER": "u", "SETRON_DB_PASSWORD": "p", "SETRON_DB_CONNECT_TIMEOUT": "1"})

    class Lazy:
        def transaction(self):
            return StorageService(broken).transaction()

    result = save_management_data(Lazy(), tmp_path / "x.xlsx")
    assert not result.saved and result.message.startswith("DB 자료를 읽지 못했습니다")
