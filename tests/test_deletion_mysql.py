"""현황표 기준일 삭제를 실제 MySQL 5.6.19에서 검증한다 (멘토 5차 요청 3번: 1~3월을 넣고 2월만 지우기)."""

import os
from datetime import date
from uuid import uuid4

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.deletion import SnapshotDeletionService
from setron.features.equipment.service import EquipmentService
from setron.features.import_save.service import DatabaseImportSaver
from test_import_save_mysql import EQUIPMENT, count, request, row, storage  # noqa: F401
from test_schema_mysql import schema_database  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
DAYS = (date(2026, 1, 5), date(2026, 2, 2), date(2026, 3, 2))


def _save_three_months(storage):
    """같은 설비 두 대를 1·2·3월 현황표로 실제 가져오기 저장 경로에 넣는다."""
    codes = [f"QA-DEL-{uuid4().hex[:8]}" for _ in range(2)]
    for day in DAYS:
        rows = [row(n, {"equipment_code": f"C{n}", "version_raw": f"F{n}"}, **{**EQUIPMENT, "base_date": day.isoformat()},
                    equipment_code=code, model_name="M-C4", family_name="C4", version_raw="v4.56.6.43")
                for n, code in enumerate(codes, 1)]
        DatabaseImportSaver(storage).save(request(ExtractKind.EQUIPMENT_STATUS, rows, f"req-del-{day}-{codes[0]}"))
    return codes


def _mine(service, codes, storage):
    """이 테스트가 넣은 단위만 고른다 (같은 DB의 다른 테스트 자료는 건드리지 않는다)."""
    revision, batches = service.batches()
    equipment = EquipmentService(storage)
    keep = []
    for batch in batches:
        if batch.base_date in DAYS and any(equipment.get_snapshot(code, batch.base_date) is not None for code in codes):
            keep.append(batch)
    return revision, {b.base_date: b for b in keep}


def test_delete_february_only_keeps_other_months_and_equipment(storage):
    codes = _save_three_months(storage)
    service = SnapshotDeletionService(storage)
    revision, mine = _mine(service, codes, storage)
    assert set(mine) == set(DAYS) and all(b.equipment_count == 2 and b.row_count == 2 for b in mine.values())
    february = mine[date(2026, 2, 2)]
    outcome = service.delete(revision, (february.key,))
    assert outcome.status == "success" and outcome.deleted_keys == (february.key,) and outcome.revision != revision
    equipment = EquipmentService(storage)
    for code in codes:
        assert equipment.get_snapshot(code, date(2026, 2, 2)) is None
        january = equipment.get_snapshot(code, date(2026, 1, 5))
        assert january is not None and equipment.get_snapshot(code, date(2026, 3, 2)) is not None
        # 설비 호기는 남는다. 호기 코드 칸은 VARBINARY라 저장소를 거치지 않는 직접 조회는 설비 ID로 센다.
        assert count(storage, "equipment", equipment_id=january.equipment_id) == 1
    assert count(storage, "equipment_snapshot", batch_id=february.batch_id) == 0
    assert count(storage, "equipment_version", batch_id=february.batch_id) == 0
    assert count(storage, "equipment_pm", batch_id=february.batch_id) == 0
    assert count(storage, "import_batch", batch_id=february.batch_id) == 1    # 가져오기 기록(원본 셀)은 남는다


def test_stale_list_or_missing_key_deletes_nothing(storage):
    codes = _save_three_months(storage)
    service = SnapshotDeletionService(storage)
    revision, mine = _mine(service, codes, storage)
    january = mine[date(2026, 1, 5)]
    _save_three_months(storage)                                               # 목록을 연 뒤 다른 저장이 생김
    stale = service.delete(revision, (january.key,))
    assert stale.status == "failed" and "다시 불러" in stale.message
    fresh, _ = service.batches()
    missing = service.delete(fresh, (january.key, "status:999999:2026-01-05:화성"))
    assert missing.status == "failed" and "이미 없거나" in missing.message
    assert EquipmentService(storage).get_snapshot(codes[0], date(2026, 1, 5)) is not None
