"""가져오기 묶음의 DB 저장을 실제 MySQL 5.6.19 스키마와 시연용 기준 자료로 검증한다 (Jira S15P31B106-75)."""

import os
from datetime import date
from pathlib import Path

import pymysql
import pytest
from pymysql.constants import CLIENT
from sqlalchemy import func, select

from setron.features.ai.schemas import ExtractKind
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import DuplicateSaveError, SaveRequest, SaveRow
from setron.features.import_save.service import DatabaseImportSaver, batch_raw_data
from setron.features.release.service import ReleaseService
from setron.features.storage.service import StorageService
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.ai_client import ResponseSource
from setron.infrastructure.database import DatabaseConfig
from test_schema_mysql import schema_database  # noqa: F401  공통 스키마 생성·정리 fixture 재사용

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")

SEED = Path(__file__).resolve().parents[1] / "database" / "seed_demo_catalog.sql"
SHEET = "현황"


@pytest.fixture(scope="module")
def storage(schema_database):  # noqa: F811
    """스키마에 시연용 기준 자료를 두 번 적용한다 (두 번째는 아무것도 넣지 않아야 한다)."""
    config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
    for _ in range(2):
        connection = pymysql.connect(host=config.host, port=config.port, user=config.username,
                                     password=config.password, database=config.database, charset="utf8mb4",
                                     client_flag=CLIENT.MULTI_STATEMENTS)
        try:
            with connection.cursor() as cursor:
                cursor.execute(SEED.read_text(encoding="utf-8"))
                while cursor.nextset():
                    pass
            connection.commit()
        finally:
            connection.close()
    return StorageService(schema_database)


def count(storage, table, **filters):
    with storage.transaction() as tx:
        target = tx.repository.table(table)
        return tx.session.scalar(select(func.count()).select_from(target).filter_by(**filters))


def row(row_id, cells, edited=(), **values):
    """검토 행 하나. ``cells``는 필드별 원본 셀 주소("A1" 또는 "A1,A2")다."""
    sources = {name: {"sheet": SHEET, "cells": address} for name, address in cells.items()}
    return SaveRow((row_id,), values, sources or None, tuple(edited))


def request(kind, rows, request_id, cells=None):
    used = {a for r in rows for ref in (r.source_reference or {}).values() for a in ref["cells"].split(",")}
    raw = {"sheet": SHEET, "cells": cells or {a: f"원문 {a}" for a in used}, "merged": ["A1:A2"]}
    return SaveRequest(request_id, kind, "가상.xlsx", raw, tuple(rows), 0, ResponseSource.EXAMPLE)


EQUIPMENT = dict(base_date="2026-10-21", site="화성", line="13L", production_area="MEMORY")


def mixed_rows(code="QA-MIXED"):
    """PM1에 O3, PM3에 O2를 쓰는 혼합 설비 행 두 개."""
    base = dict(EQUIPMENT, equipment_code=code, model_name="M-O3/O2 혼합", group_name="Oxide")
    return [
        row(1, {"equipment_code": "C5", "site": "A1", "version_raw": "F5", "pm_name": "E5"},
            **base, pm_name="PM1", family_name="O3", version_raw="v1.2"),
        row(2, {"equipment_code": "C5", "version_raw": "F6", "pm_name": "E6"},
            **base, pm_name="PM3", family_name="O2", version_raw="v2.0"),
    ]


def test_seed_catalog_has_models_and_families_once(storage):
    assert count(storage, "equipment_group") == 9
    assert count(storage, "version_family") == 9
    assert count(storage, "equipment_group", group_name="HPA", analysis_enabled=0) == 1


def test_mixed_equipment_is_saved_with_batch_sources_and_pm_models(storage):
    rows = [replace_values(r, model_name=m) for r, m in zip(mixed_rows(), ("M-O3", "M-O2"))]
    saver = DatabaseImportSaver(storage)

    receipt = saver.save(request(ExtractKind.EQUIPMENT_STATUS, rows, "req-mixed"))

    with storage.transaction() as tx:
        batch = tx.get("import_batch", {"batch_id": receipt.batch_id})
    assert batch["source_type"] == "EXCEL" and set(batch["raw_data"]) == {SHEET}
    saved = EquipmentService(storage).get_snapshot("QA-MIXED", date(2026, 10, 21))
    assert saved.group_id is None and saved.site == "화성" and saved.production_area == "MEMORY"
    assert [(sw.version_raw, [pm.pm_name for pm in sw.pms]) for sw in saved.software] == [("v1.2", ["PM1"]), ("v2.0", ["PM3"])]
    assert saved.software[0].source_reference == {"version_raw": {"sheet": SHEET, "cells": ["F5"]}}
    assert all(sw.release_version_id is None for sw in saved.software)
    assert receipt.target_label.startswith("MySQL")


def test_release_is_saved_and_later_equipment_links_to_it(storage):
    saver = DatabaseImportSaver(storage)
    rows = [row(i, {"version_raw": "A2", "content": f"D{i + 1}", "item_no": f"C{i + 1}"},
                version_raw="v4.56.6.43", release_date="2026-09-01", item_no=i, content=f"변경 {i}",
                model_name="M-C4", family_name="C4", target_models="C4") for i in (1, 2)]
    receipt = saver.save(request(ExtractKind.RELEASE_NOTE, rows, "req-release"))

    releases = ReleaseService(storage).list_releases(_family_id(storage, "C4"))
    assert [r.current["version_raw"] for r in releases] == ["v4.56.6.43"]
    assert releases[0].current["released_at"] == date(2026, 9, 1)
    assert count(storage, "item", batch_id=receipt.batch_id) == 2

    equipment = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-C4",
                    model_name="M-C4", family_name="C4", version_raw="v4.56.6.43")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [equipment], "req-c4"))
    saved = EquipmentService(storage).get_snapshot("QA-C4", date(2026, 10, 21))
    assert saved.group_id == _group_id(storage, "M-C4")
    assert saved.software[0].release_version_id == releases[0].current["release_version_id"]


def test_c3_family_is_chosen_by_ui_and_m5_alias(storage):
    saver = DatabaseImportSaver(storage)
    c3 = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-C3",
             model_name="M-C3", family_name="C3", ui_type="통합UI", version_raw="v3.52_13 (H)")
    m5 = row(2, {"equipment_code": "C6", "version_raw": "F6"}, **EQUIPMENT, equipment_code="QA-M5",
             model_name="M5", family_name="O5", version_raw="v1060")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [c3, m5], "req-c3"))

    c3_saved = EquipmentService(storage).get_snapshot("QA-C3", date(2026, 10, 21))
    assert c3_saved.software[0].family_id == _family_id(storage, "C3 통합UI")
    assert c3_saved.software[0].version_raw == "v3.52_13 (H)"
    m5_saved = EquipmentService(storage).get_snapshot("QA-M5", date(2026, 10, 21))
    assert m5_saved.group_id == _group_id(storage, "M-O5") and m5_saved.model_name == "M5"


@pytest.mark.parametrize("changes, message", [
    ({"model_name": "M-C9", "group_name": "Poly"}, "등록 모델이 없거나"),
    ({"model_name": "M-C9"}, "분류를 정할 수 없습니다"),
    ({"group_name": "Etch"}, "설비군"),
    ({"group_name": "Poly", "model_name": "M-O2", "family_name": "O2"}, "소속이 다릅니다"),
    ({"model_name": "M-C3", "family_name": "C3"}, "하나로 정할 수 없습니다"),
    ({"family_name": "O9"}, "하나로 정할 수 없습니다"),
])
def test_unresolved_links_cancel_whole_batch(storage, changes, message):
    batches = count(storage, "import_batch")
    good = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-OK",
               model_name="M-O2", family_name="O2", version_raw="v1")
    bad = row(2, {"equipment_code": "C6", "version_raw": "F6"},
              **{**EQUIPMENT, "equipment_code": "QA-BAD", "model_name": "M-O2", "family_name": "O2",
                 "version_raw": "v1", **changes})

    with pytest.raises(StorageValidationError, match=message):
        DatabaseImportSaver(storage).save(request(ExtractKind.EQUIPMENT_STATUS, [good, bad], f"req-{message}"))

    assert count(storage, "import_batch") == batches
    assert EquipmentService(storage).get_snapshot("QA-OK", date(2026, 10, 21)) is None


def test_existing_snapshot_and_release_are_not_replaced(storage):
    saver = DatabaseImportSaver(storage)
    first = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-AGAIN",
                model_name="M-O2", family_name="O2", version_raw="v1")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [first], "req-again-1"))
    with pytest.raises(StorageValidationError, match="이미 저장돼 있습니다"):
        saver.save(request(ExtractKind.EQUIPMENT_STATUS, [first], "req-again-2"))

    release = row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v9", content="내용",
                  model_name="M-O2", family_name="O2")
    saver.save(request(ExtractKind.RELEASE_NOTE, [release], "req-rel-1"))
    with pytest.raises(StorageValidationError, match="이미 저장된 릴리즈"):
        saver.save(request(ExtractKind.RELEASE_NOTE, [release], "req-rel-2"))


def test_edited_fields_keep_only_release_and_item_columns(storage):
    """검토 화면에서 고친 계열·모델은 연결에만 쓰고, 출시일·내용 수정만 DB 수정 표시로 남긴다."""
    value = row(1, {"version_raw": "A2", "content": "D2", "release_date": "B2"},
                edited=("family_name", "model_name", "release_date", "content"),
                version_raw="v-edit", release_date="2026-09-02", content="고친 내용", model_name="M-O3", family_name="O3")
    DatabaseImportSaver(storage).save(request(ExtractKind.RELEASE_NOTE, [value], "req-edited"))

    detail, = ReleaseService(storage).list_releases(_family_id(storage, "O3"))
    assert detail.current["edited_fields"] == ["released_at"]
    with storage.transaction() as tx:
        item, = EquipmentService(storage).repository.rows(tx, "item", content="고친 내용")
    assert item["edited_fields"] == ["content"]


def test_release_without_family_is_rejected_with_guidance(storage):
    rows = [row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v1.0.3", content="Recipe 승인 상태 확인")]
    with pytest.raises(StorageValidationError, match="SW 계열이 없어"):
        DatabaseImportSaver(storage).save(request(ExtractKind.RELEASE_NOTE, rows, "req-no-family"))


def test_same_request_is_not_saved_twice(storage):
    saver = DatabaseImportSaver(storage)
    value = row(1, {"equipment_code": "C5", "version_raw": "F5"}, **EQUIPMENT, equipment_code="QA-ONCE",
                model_name="M-O2", family_name="O2", version_raw="v1")
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [value], "req-once"))
    with pytest.raises(DuplicateSaveError):
        saver.save(request(ExtractKind.EQUIPMENT_STATUS, [value], "req-once"))


def _group_id(storage, name):
    with storage.transaction() as tx:
        return EquipmentService(storage).repository.rows(tx, "equipment_group", group_name=name)[0]["group_id"]


def _family_id(storage, name):
    with storage.transaction() as tx:
        return EquipmentService(storage).repository.rows(tx, "version_family", family_name=name)[0]["family_id"]


def replace_values(value, **changes):
    return SaveRow(value.row_ids, {**value.values, **changes}, value.source_reference, value.edited_fields)


def test_saver_is_created_only_with_db_settings():
    assert DatabaseImportSaver.from_env({}) is None
    saver = DatabaseImportSaver.from_env({"SETRON_DB_NAME": "setron"})
    assert saver is not None and saver._storage is None  # 첫 저장 전에는 접속하지 않는다


def test_raw_data_is_stored_as_sheet_cell_map():
    raw = {"sheet": "Memory", "cells": {"A1": "화성", "B5": 13}, "merged": ["A1:B1"]}
    assert batch_raw_data(raw) == {"Memory": {"A1": "화성", "B5": 13}}
