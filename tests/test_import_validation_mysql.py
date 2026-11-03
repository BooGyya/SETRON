"""실제 MySQL 공통 스키마에서 BE-008의 기존값 조회와 쓰기 없는 검증을 확인한다."""

from datetime import date
import os

import pytest
from sqlalchemy import event

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.service import ImportValidationService
from setron.features.excel_import.schemas import ExtractKind, ImportBlockedError
from setron.features.excel_import.service import has_errors
from setron.features.release.schemas import ItemInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.storage.service import StorageService
from setron.features.version.schemas import CompareReason

from test_import_validation import draft, row
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")


def test_actual_schema_validation_is_read_only_and_holds_all_sheets(schema_database):
    """실제 MySQL의 원문·PM·Item을 읽어 변경·미확인 순서를 표시하고 충돌 묶음을 차단한다.

    param
    - schema_database: 기존 업무 테이블이 없는 전용 테스트 DB에만 적용한 공통 스키마.
    """
    storage = StorageService(schema_database)
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {}})
        model = tx.insert("equipment_group", {"group_name": "M-O2", "category": "OXIDE"})
        family = tx.insert("version_family", {"group_id": model["group_id"], "family_name": "O2"})
    release_service = ReleaseService(storage)
    release_service.register_release(ReleaseInput(family_id=family["family_id"], version_raw="v1_자유 핫픽스"),
                                     (ItemInput(item_no=7, content="기존 Item 내용"),))
    target = release_service.register_release(ReleaseInput(family_id=family["family_id"], version_raw="v2", version_order=2))
    target_id = target.release.current["release_version_id"]
    equipment_service = EquipmentService(storage)
    equipment_service.save_snapshots((SnapshotInput(
        equipment_code="E-1", base_date=date(2026, 11, 2), site="평택", model_name="M-O2", batch_id=batch["batch_id"],
        software=(SoftwareInput(family_id=family["family_id"], version_raw="v2", release_version_id=target_id,
            pms=(PmInput(pm_name="PM1", group_id=model["group_id"], model_name_raw="M-O2"),)),),
    ),))
    commands = []

    def record(connection, cursor, statement, parameters, context, executemany):
        """검증 구간의 실제 SQL 종류를 기록한다.

        param
        - connection, cursor, statement, parameters, context, executemany: SQLAlchemy 실행 이벤트 인자.
        """
        commands.append(statement.strip().split()[0].upper())

    event.listen(schema_database.engine, "before_cursor_execute", record)
    try:
        service = ImportValidationService(storage)
        source = draft(row(version_raw="v1_자유 핫픽스"))
        targets = {(model["group_id"], family["family_id"]): target_id}
        issues = service.validate((source,), targets=targets)
        assert not has_errors(issues)
        assert next(i.comparison for i in issues if i.comparison).reason is CompareReason.ORDER_UNKNOWN
        assert any(i.previous_value == "평택" and i.current_value == "화성" for i in issues)
        assert len(service.prepare((source,), "r", targets=targets)) == 1
        with pytest.raises(ImportBlockedError):
            service.prepare((source, draft(row(version_raw="v2"), sheet="다른 시트")), "conflict", targets=targets)
        release_draft = draft(row(version_raw="v1_자유 핫픽스", item_no="7", content="새 Item 내용"), kind=ExtractKind.RELEASE_NOTE)
        changes = service.validate((release_draft,))
        assert any(i.previous_value == "기존 Item 내용" and i.current_value == "새 Item 내용" for i in changes)
        assert commands and all(command == "SELECT" for command in commands)
    finally:
        event.remove(schema_database.engine, "before_cursor_execute", record)
    stored = equipment_service.get_snapshot("E-1", date(2026, 11, 2))
    assert stored.site == "평택" and stored.software[0].version_raw == "v2"


def test_actual_schema_validates_models_before_shared_family_and_allows_confirmed_alias(schema_database):
    """MySQL 등록 모델을 독립적으로 확인하면서 원형·개조형의 계열 공유와 M5 별칭을 보존한다.

    param
    - schema_database: 기존 업무 테이블이 없는 전용 테스트 DB에만 적용한 공통 스키마.
    """
    storage = StorageService(schema_database)
    with storage.transaction() as tx:
        base = tx.insert("equipment_group", {"group_name": "M-O5", "category": "OXIDE"})
        prime = tx.insert("equipment_group", {
            "group_name": "M-O5 Prime", "category": "OXIDE", "parent_group_id": base["group_id"],
        })
        family = tx.insert("version_family", {"group_id": base["group_id"], "family_name": "O5"})
    release_service = ReleaseService(storage)
    first = release_service.register_release(ReleaseInput(family_id=family["family_id"], version_raw="v1", version_order=1))
    second = release_service.register_release(ReleaseInput(family_id=family["family_id"], version_raw="v2", version_order=2))
    targets = {
        (base["group_id"], family["family_id"]): first.release.current["release_version_id"],
        (prime["group_id"], family["family_id"]): second.release.current["release_version_id"],
    }
    service = ImportValidationService(storage)
    unknown = draft(row(model_name="DB에 없는 모델", family_name="O5", equipment_code="BE008-MODEL-UNKNOWN"))
    issues = service.validate((unknown,), targets=targets)
    assert any(issue.code == "MODEL_NOT_REGISTERED" and issue.field == "model_name" for issue in issues)
    with pytest.raises(ImportBlockedError):
        service.prepare((unknown,), "unknown-model", targets=targets)
    drafts = (
        draft(row(model_name="M5", family_name="O5", equipment_code="BE008-MODEL-BASE"), sheet="원형"),
        draft(row(model_name="M-O5 Prime", family_name="O5", equipment_code="BE008-MODEL-PRIME"), sheet="개조형"),
    )
    issues = service.validate(drafts, targets=targets)
    assert not has_errors(issues)
    assert {issue.sheet_name: issue.comparison.label for issue in issues if issue.comparison} == {"원형": "N 이상", "개조형": "N-1"}
    requests = service.prepare(drafts, "shared-family", targets=targets)
    assert [request.rows[0].values["model_name"] for request in requests] == ["M-O5", "M-O5 Prime"]
    hotfix = draft(row(model_name="M-O5 Prime", family_name="O5", equipment_code="BE008-MODEL-HOTFIX", version_raw="v1_이름 규칙 없음"))
    assert len(service.prepare((hotfix,), "unregistered-hotfix", targets=targets)) == 1
    with storage.transaction() as tx:
        tx.insert("equipment_group", {"group_name": "M-O5", "category": "OXIDE"})
    ambiguous = service.validate((drafts[0],), targets=targets)
    assert any(issue.code == "MODEL_AMBIGUOUS" for issue in ambiguous)
    with pytest.raises(ImportBlockedError):
        service.prepare((drafts[0],), "ambiguous-model", targets=targets)


def test_actual_schema_family_requires_input_model_or_direct_parent_owner(schema_database):
    """실제 DB의 계열 소유 모델과 직계 부모 연결을 검사하고 릴리즈 모델 공란도 차단한다.

    param
    - schema_database: 기존 업무 테이블이 없는 전용 테스트 DB에만 적용한 공통 스키마.
    """
    storage = StorageService(schema_database)
    with storage.transaction() as tx:
        owner = tx.insert("equipment_group", {"group_name": "검증 전용 모델 A", "category": "OXIDE"})
        other = tx.insert("equipment_group", {"group_name": "검증 전용 모델 B", "category": "OXIDE"})
        tx.insert("version_family", {"group_id": owner["group_id"], "family_name": "소유 검증 계열"})
    service = ImportValidationService(storage)
    source = draft(row(model_name=other["group_name"], family_name="소유 검증 계열", equipment_code="BE008-OWNER-NEW"))
    assert any(issue.code == "FAMILY_UNRESOLVED" for issue in service.validate((source,)))
    with pytest.raises(ImportBlockedError):
        service.prepare((source,), "unrelated-owner")
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": other["group_id"]}, {"parent_group_id": owner["group_id"]})
    assert not has_errors(service.validate((source,)))
    assert len(service.prepare((source,), "direct-parent-owner")) == 1
    no_model = draft(row(model_name=None, family_name="소유 검증 계열", content="합성 Item"), kind=ExtractKind.RELEASE_NOTE)
    assert any(issue.code == "MODEL_REQUIRED" for issue in service.validate((no_model,)))
    with pytest.raises(ImportBlockedError):
        service.prepare((no_model,), "missing-release-model")
