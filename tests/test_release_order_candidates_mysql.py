"""가져온 릴리즈로 버전 순서 후보를 만들고 관리 저장으로 반영하는 흐름을 실제 MySQL 5.6.19에서 검증한다."""

import os

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.equipment.repository import EquipmentRepository
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.management import ManagementService, RecordEdit
from setron.ui.management_data import ManagementGateway
from test_import_save_mysql import request, row, storage  # noqa: F401  시드 적용 fixture 재사용
from test_schema_mysql import schema_database  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_imported_release_listing_becomes_reviewed_version_order(storage):  # noqa: F811
    rows = [row(i, {"version_raw": f"A{r}", "content": f"D{r}"}, version_raw=version, content=f"변경 {version}",
                model_name="M-O2", family_name="O2")
            for i, (r, version) in enumerate(((2, "v1.0"), (5, "v1.1"), (9, "v2.0")), start=1)]
    cells = {f"A{r}": v for r, v in ((2, "v1.0"), (5, "v1.1"), (9, "v2.0"))}
    cells.update({f"D{r}": f"변경 {v}" for r, v in ((2, "v1.0"), (5, "v1.1"), (9, "v2.0"))})
    DatabaseImportSaver(storage).save(request(ExtractKind.RELEASE_NOTE, rows, "order-1", cells=cells))
    service = ManagementService(storage)
    with storage.transaction() as tx:
        family_id = next(f["family_id"] for f in EquipmentRepository().rows(tx, "version_family")
                         if f["family_name"] == "O2")

    records, suggestion = ManagementGateway(storage).version_order_candidates(family_id)

    assert suggestion.reason is None
    assert [(c.version_raw, c.version_order) for c in suggestion.candidates] == [("v1.0", 10), ("v1.1", 20), ("v2.0", 30)]
    by_id = {record.key["release_version_id"]: record for record in records}
    service.save(tuple(RecordEdit("release_version", by_id[c.release_version_id].key, by_id[c.release_version_id].revision,
                                  {"version_order": c.version_order}) for c in suggestion.candidates))
    saved = {r.current["version_raw"]: r.current["version_order"]
             for r in service.list_records("release_version", filters={"family_id": family_id})}
    assert saved == {"v1.0": 10, "v1.1": 20, "v2.0": 30}
    assert "이미 순서" in ManagementGateway(storage).version_order_candidates(family_id)[1].reason
