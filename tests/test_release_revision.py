"""BE-010의 재판정을 실제 SQLite 트랜잭션으로 검증한다. MySQL 검증과 구분한다."""

from datetime import date

import pytest
from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary,
    MetaData, String, Table, UniqueConstraint, create_engine, event, text,
)

from setron.features.item.schemas import Application, ItemState, TargetScope
from setron.features.release.refresh import ReleaseRefreshService
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.risk.schemas import RiskStatus
from setron.features.storage.service import StorageService
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import Database


@pytest.fixture
def revision_storage():
    """외래키와 고유키를 포함한 독립적인 메모리 업무 DB를 준비한다.

    return
    - 모델 1 O2·2 O2 Plus·3 C3·4 HPA, 계열 1 O2·2 C3를 등록한 StorageService.
    """
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        """SQLite 연결에서 실제 외래키 검사를 켠다.

        param
        - connection, record: SQLAlchemy가 전달한 연결과 연결 기록.
        """
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    Table("import_batch", metadata,
          Column("batch_id", Integer, primary_key=True),
          Column("source_type", String(20), nullable=False),
          Column("imported_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
          Column("raw_data", String))
    Table("equipment_group", metadata,
          Column("group_id", Integer, primary_key=True),
          Column("group_name", String(50), nullable=False), Column("category", String(10), nullable=False),
          Column("parent_group_id", Integer, ForeignKey("equipment_group.group_id")),
          Column("analysis_enabled", Integer, nullable=False, server_default="1"))
    Table("equipment", metadata, Column("equipment_id", Integer, primary_key=True),
          Column("equipment_code", LargeBinary(200), nullable=False, unique=True))
    Table("version_family", metadata, Column("family_id", Integer, primary_key=True),
          Column("group_id", Integer, ForeignKey("equipment_group.group_id"), nullable=False),
          Column("family_name", String(50), nullable=False), Column("ui_type", String(20)))
    Table("release_note", metadata, Column("release_note_id", Integer, primary_key=True),
          Column("group_id", Integer, ForeignKey("equipment_group.group_id"), nullable=False),
          Column("release_date", Date),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False))
    Table("release_version", metadata, Column("release_version_id", Integer, primary_key=True),
          Column("release_note_id", Integer, ForeignKey("release_note.release_note_id")),
          Column("family_id", Integer, ForeignKey("version_family.family_id"), nullable=False),
          Column("version_raw", LargeBinary(400), nullable=False), Column("version_suffix", String(50)),
          Column("released_at", Date), Column("version_order", Integer),
          Column("item_mapping_status", String(20), nullable=False),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False),
          Column("source_reference", String), Column("edited_fields", String, nullable=False),
          UniqueConstraint("family_id", "version_raw"))
    Table("item", metadata, Column("item_id", Integer, primary_key=True),
          Column("release_note_id", Integer, ForeignKey("release_note.release_note_id")),
          Column("item_no", Integer), Column("content", String, nullable=False),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False),
          Column("source_reference", String), Column("edited_fields", String, nullable=False))
    Table("equipment_snapshot", metadata, Column("snapshot_id", Integer, primary_key=True),
          Column("equipment_id", Integer, ForeignKey("equipment.equipment_id"), nullable=False),
          Column("base_date", Date, nullable=False), Column("site", String(50), nullable=False),
          Column("line", String(50)), Column("model_name", String(255), nullable=False),
          Column("group_id", Integer, ForeignKey("equipment_group.group_id")),
          Column("production_area", String(20)), Column("structure_type", String(50)),
          Column("geometry_type", String(50)), Column("modification_type", String(100)),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False),
          Column("source_reference", String), UniqueConstraint("equipment_id", "base_date"))
    Table("equipment_version", metadata, Column("equipment_version_id", Integer, primary_key=True),
          Column("snapshot_id", Integer, ForeignKey("equipment_snapshot.snapshot_id"), nullable=False),
          Column("family_id", Integer, ForeignKey("version_family.family_id"), nullable=False),
          Column("version_raw", LargeBinary(400), nullable=False),
          Column("release_version_id", Integer, ForeignKey("release_version.release_version_id", ondelete="SET NULL")),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False),
          Column("source_reference", String), UniqueConstraint("snapshot_id", "equipment_version_id"),
          UniqueConstraint("snapshot_id", "family_id", "version_raw"))
    Table("equipment_pm", metadata, Column("pm_id", Integer, primary_key=True),
          Column("snapshot_id", Integer, ForeignKey("equipment_snapshot.snapshot_id"), nullable=False),
          Column("equipment_version_id", Integer), Column("pm_name", LargeBinary(200), nullable=False),
          Column("group_id", Integer, ForeignKey("equipment_group.group_id"), nullable=False),
          Column("model_name_raw", String(255)),
          Column("batch_id", Integer, ForeignKey("import_batch.batch_id"), nullable=False),
          Column("source_reference", String), UniqueConstraint("snapshot_id", "pm_name"),
          ForeignKeyConstraint(["snapshot_id", "equipment_version_id"],
                               ["equipment_version.snapshot_id", "equipment_version.equipment_version_id"]))
    Table("release_version_item", metadata,
          Column("release_version_id", Integer, ForeignKey("release_version.release_version_id", ondelete="CASCADE"), primary_key=True),
          Column("item_id", Integer, ForeignKey("item.item_id", ondelete="CASCADE"), primary_key=True),
          Column("item_state", String(20), nullable=False), Column("target_scope", String(20), nullable=False),
          Column("target_group_ids", String))
    Table("risk_rule", metadata, Column("risk_rule_id", Integer, primary_key=True),
          Column("rule_type", String(30), nullable=False),
          Column("family_id", Integer, ForeignKey("version_family.family_id"), nullable=False),
          Column("group_id", Integer, ForeignKey("equipment_group.group_id")),
          Column("item_id", Integer, ForeignKey("item.item_id", ondelete="SET NULL")),
          *(Column(name, Integer, ForeignKey("release_version.release_version_id", ondelete="SET NULL"))
            for name in ("release_version_id", "start_release_version_id", "end_release_version_id")),
          Column("risk_reason", String, nullable=False), Column("is_enabled", Integer, nullable=False),
          Column("target_reference", String, nullable=False), Column("mapping_status", String(20), nullable=False))
    Table("app_setting", metadata, Column("setting_key", LargeBinary(400), primary_key=True),
          Column("setting_value", String, nullable=False))
    metadata.create_all(engine)
    storage = StorageService(Database(engine))
    # SQLite에서 반사한 BLOB의 길이는 없으므로 생산 스키마의 원문 길이를 복원한다.
    for name in metadata.tables:
        for column in storage.repository.table(name).c:
            if isinstance(column.type, LargeBinary):
                column.type.length = metadata.tables[name].c[column.name].type.length
    with storage.transaction() as tx:
        for identifier, name, category, parent, enabled in (
            (1, "O2", "OXIDE", None, True), (2, "O2 Plus", "OXIDE", 1, True),
            (3, "C3", "POLY", None, True), (4, "HPA", "HPA", None, False),
        ):
            tx.insert("equipment_group", {"group_id": identifier, "group_name": name, "category": category,
                                          "parent_group_id": parent, "analysis_enabled": enabled})
        tx.insert("version_family", {"family_id": 1, "group_id": 1, "family_name": "O2", "ui_type": None})
        tx.insert("version_family", {"family_id": 2, "group_id": 3, "family_name": "C3", "ui_type": "LEGACY"})
    try:
        yield storage
    finally:
        engine.dispose()


def release(storage, *, family=1, raw="v1", order=1):
    """검증용 수동 릴리즈를 등록한다.

    param
    - storage: 독립적인 테스트 저장소.
    - family, raw, order: 계열과 버전 원문, 확인된 순서. order=None은 미확인.

    return
    - 생성된 릴리즈의 정수 ID.
    """
    result = ReleaseService(storage).register_release(ReleaseInput(family_id=family, version_raw=raw, version_order=order))
    return result.release.current["release_version_id"]


def observation(storage, *, day=date(2026, 10, 1), code="EQ-1", versions=((1, 1, "v1", None),), pms=False):
    """원본과 PM 고정 위치를 가진 실제 DB 관측을 만든다.

    param
    - storage: 독립적인 테스트 저장소.
    - day, code: 실제 관측 날짜와 전역 고유 호기 코드.
    - versions: (계열 ID, 모델 ID, 원문, 대응 릴리즈 ID) 튜플 목록.
    - pms: True이면 각 SW에 PM1부터 고정 위치를 붙임.

    return
    - 새 현황 ID와 적용 SW 행의 튜플.
    """
    with storage.transaction() as tx:
        equipment = next((row for row in all_rows_in(tx, "equipment") if row["equipment_code"] == code), None)
        if equipment is None:
            equipment = tx.insert("equipment", {"equipment_code": code})
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {"현황": {"A1": code, "B1": "원본"}}})
        snapshot = tx.insert("equipment_snapshot", {
            "equipment_id": equipment["equipment_id"], "base_date": day, "site": "화성", "line": "Line1",
            "model_name": "원본 혼합 모델", "group_id": None if pms else versions[0][1],
            "batch_id": batch["batch_id"], "source_reference": {"model_name": {"sheet": "현황", "cells": ["B1"]}},
        })
        rows = []
        for number, (family_id, model_id, raw, release_id) in enumerate(versions, 1):
            row = tx.insert("equipment_version", {"snapshot_id": snapshot["snapshot_id"], "family_id": family_id,
                "version_raw": raw, "release_version_id": release_id, "batch_id": batch["batch_id"],
                "source_reference": {"version_raw": {"sheet": "현황", "cells": ["B1"]}}})
            rows.append(row)
            if pms:
                tx.insert("equipment_pm", {"snapshot_id": snapshot["snapshot_id"],
                    "equipment_version_id": row["equipment_version_id"], "pm_name": f"PM{number}",
                    "group_id": model_id, "model_name_raw": f"모델{model_id}", "batch_id": batch["batch_id"]})
        return snapshot["snapshot_id"], tuple(rows)


def all_rows_in(tx, table):
    """현재 트랜잭션의 관리 행을 기본키 순서로 복원한다.

    param
    - tx, table: 공통 트랜잭션과 읽을 테이블 이름.

    return
    - 복원한 행 목록.
    """
    from setron.features.equipment.repository import EquipmentRepository
    return EquipmentRepository().rows(tx, table)


def all_rows(storage, table):
    """저장 확정된 관리 행을 조회한다.

    param
    - storage, table: 공통 저장 서비스와 읽을 테이블 이름.

    return
    - 복원한 행 목록.
    """
    with storage.transaction() as tx:
        return all_rows_in(tx, table)


def refresh(storage, **kwargs):
    """독립적인 저장 업무 안에서 재판정을 실행한다.

    param
    - storage: 독립적인 테스트 저장소.
    - kwargs: refresh_in에 전달할 명시적인 조건.

    return
    - 저장이 확정된 재판정 결과.
    """
    with storage.transaction() as tx:
        return ReleaseRefreshService().refresh_in(tx, **kwargs)


def test_refresh_registers_exact_raw_and_preserves_original_history(revision_storage):
    """등록 후 원문이 같은 SW만 연결하고 관측 원본·PM·입력 작업을 보존하는지 검증한다.

    param
    - revision_storage: 외부 서버를 쓰지 않는 독립적인 테스트 저장소.
    """
    storage = revision_storage
    observation(storage)
    observation(storage, code="EQ-2", versions=((1, 1, "v1 ", None),))
    preserved = {name: all_rows(storage, name) for name in ("equipment_snapshot", "equipment_pm", "import_batch")}
    before = all_rows(storage, "equipment_version")
    selected = release(storage)
    context = RefreshContext(targets={(1, 1): selected}, applicability={(1, 1): (selected,)})
    result = refresh(storage, context=context)
    after = all_rows(storage, "equipment_version")
    assert after[0]["release_version_id"] == selected and after[1]["release_version_id"] is None
    assert [{k: v for k, v in row.items() if k != "release_version_id"} for row in after] == [
        {k: v for k, v in row.items() if k != "release_version_id"} for row in before]
    for name, rows in preserved.items():
        assert all_rows(storage, name)[:len(rows)] == rows
    assert result.targets[0].valid and result.targets[0].selected_release_id == selected
    assert result.dates[0].summary.compliant == 1 and result.dates[0].summary.undetermined == 1
    assert not result.dates[0].risks.has_rules


def test_refresh_keeps_confirmed_alias_until_renamed_id_is_invalidated(revision_storage):
    """명시적으로 확인한 대응은 보존하고 원문 변경 시 정확한 새 근거로 재연결하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    selected = release(storage)
    observation(storage, versions=((1, 1, "표기가 다른 원문", selected),))
    refresh(storage)
    assert all_rows(storage, "equipment_version")[0]["release_version_id"] == selected
    refresh(storage, invalidated_release_ids=(selected,))
    assert all_rows(storage, "equipment_version")[0]["release_version_id"] is None
    refresh(storage, context=RefreshContext(aliases={1: {"표기가 다른 원문": "v1"}}))
    assert all_rows(storage, "equipment_version")[0]["release_version_id"] == selected


def test_targets_remain_independent_and_missing_applicability_requests_reselection(revision_storage):
    """원형·파생형 목표를 따로 확인하고 미확인·다른 모델 적용 목표를 변경하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    first, second = release(storage), release(storage, raw="v2", order=2)
    targets = {(1, 1): first, (2, 1): second, (3, 1): first, (99, 1): first}
    context = RefreshContext(targets=targets, applicability={(1, 1): (first,), (2, 1): (first,)})
    result = refresh(storage, context=context)
    validity = {(value.model_id, value.family_id): value for value in result.targets}
    assert validity[(1, 1)].valid
    assert not validity[(2, 1)].valid and not validity[(3, 1)].valid and not validity[(99, 1)].valid
    assert all(value.reason and "선택" in value.reason for key, value in validity.items() if key != (1, 1))
    assert {key: value.selected_release_id for key, value in validity.items()} == targets
    assert context.targets == targets


def test_duplicate_order_invalidates_target_without_selecting_another_release(revision_storage):
    """릴리즈 순서가 겹치면 N을 바꾸지 않고 재선택을 안내하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    selected = release(storage)
    release(storage, raw="hotfix", order=1)
    observation(storage)
    result = refresh(storage, context=RefreshContext(targets={(1, 1): selected}, applicability={(1, 1): (selected,)}))
    assert not result.targets[0].valid and "중복" in result.targets[0].reason
    assert result.targets[0].selected_release_id == selected
    assert result.dates[0].summary.undetermined == 1


def test_each_historical_date_recalculates_mixed_pm_item_and_risk_together(revision_storage):
    """두 날짜를 섞지 않고 PM 비교·명시한 Item 상태·수동 위험을 같은 자료로 판정하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    service = ReleaseService(storage)
    first = release(storage)
    second = release(storage, raw="v2", order=2)
    other = release(storage, family=2, raw="c1", order=1)
    item_id = service.register_item(first, ItemInput(item_no=7, content="환경 안전" )).current["item_id"]
    service.save_item_links(first, (ItemLinkInput(item_id=item_id, item_state=ItemState.NOT_APPLIED, target_scope=TargetScope.ALL),))
    service.save_item_links(second, (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL),))
    observation(storage, versions=((1, 1, "v1", None), (2, 3, "c1", None)), pms=True)
    observation(storage, day=date(2026, 10, 2), versions=((1, 1, "v2", None), (2, 3, "c1", None)), pms=True)
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1, "item_id": item_id,
            "risk_reason": "수동 안전 사유", "target_reference": {"사용자 근거": "보존"}, "mapping_status": "VERIFIED"})
    context = RefreshContext(targets={(1, 1): second, (3, 2): other},
                             applicability={(1, 1): (first, second), (3, 2): (other,)})
    result = refresh(storage, context=context)
    assert [value.base_date for value in result.dates] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert [value.summary.equipment_count for value in result.dates] == [1, 1]
    assert [value.summary.below for value in result.dates] == [1, 0]
    assert [value.summary.compliant for value in result.dates] == [1, 2]
    assert [value.item_summaries[0].equipment[0].application for value in result.dates] == [Application.NOT_APPLIED, Application.APPLIED]
    assert [value.risks.equipment[0].status for value in result.dates] == [RiskStatus.RISK, RiskStatus.NO_RISK]


def test_invalid_risk_end_remains_unknown_and_manual_rule_unchanged(revision_storage):
    """위험 해제 순서가 불확실하면 안전으로 해석하지 않고 수동 지정 자체를 보존하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    first, second = release(storage), release(storage, raw="v2", order=None)
    item_id = ReleaseService(storage).register_item(first, ItemInput(item_no=7, content="결함 내용")).current["item_id"]
    observation(storage)
    with storage.transaction() as tx:
        rule = tx.insert("risk_rule", {"rule_type": "DEFECT_ITEM", "family_id": 1, "item_id": item_id,
            "start_release_version_id": first, "end_release_version_id": second, "risk_reason": "수동 결함",
            "target_reference": {"사용자": "원문"}, "mapping_status": "VERIFIED"})
    result = refresh(storage)
    assert result.unresolved_risk_ids == (rule["risk_rule_id"],)
    assert result.dates[0].risks.equipment[0].status is RiskStatus.UNKNOWN
    assert all_rows(storage, "risk_rule") == [rule]


def test_refresh_late_judgment_failure_rolls_back_earlier_registration_links(revision_storage):
    """후속 관측의 오류가 발견되면 앞서 갱신한 릴리즈 연결도 함께 취소하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    release(storage)
    observation(storage)
    broken_snapshot, _ = observation(storage, code="EQ-2")
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": broken_snapshot}, {"group_id": None})
    before = all_rows(storage, "equipment_version")
    with pytest.raises(StorageValidationError):
        refresh(storage)
    assert all_rows(storage, "equipment_version") == before


def test_refresh_reports_hpa_exclusion_without_inflating_item_and_risk_counts(revision_storage):
    """분석 제외 모델은 실제 HPA 제외 수로만 전달하고 업무 판정 대수에서 제외하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    """
    storage = revision_storage
    selected = release(storage)
    ReleaseService(storage).register_item(selected, ItemInput(item_no=1, content="내용"))
    observation(storage, versions=((1, 4, "v1", None),))
    result = refresh(storage)
    day = result.dates[0]
    assert day.summary.equipment_count == 0 and day.summary.hpa_excluded == 1
    assert day.item_summaries[0].equipment == () and day.item_summaries[0].hpa_excluded == 1
    assert day.risks.equipment == () and day.risks.hpa_excluded == 1


@pytest.mark.parametrize("context", [
    RefreshContext(targets={(True, 1): 1}), RefreshContext(targets={(1, 1): True}),
    RefreshContext(applicability={(1, 1): [1]}), RefreshContext(aliases={1: {"": "v1"}}),
])
def test_invalid_refresh_conditions_are_rejected_without_writes(revision_storage, context):
    """모호한 ID와 잘못된 적용 범위·별칭 형식을 DB 갱신 전에 거부하는지 검증한다.

    param
    - revision_storage: 독립적인 테스트 저장소.
    - context: 자료형 또는 필수값이 잘못된 재판정 조건.
    """
    storage = revision_storage
    before = all_rows(storage, "equipment_version")
    with pytest.raises(StorageValidationError):
        refresh(storage, context=context)
    assert all_rows(storage, "equipment_version") == before
