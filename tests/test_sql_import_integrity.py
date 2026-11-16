"""SQL 병합이 기존 설비의 업무 연결을 깨뜨리기 전에 차단되는지 검증한다."""

import os
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import MetaData

from setron.features.db_import.dump import read_dump
from setron.features.db_import.merge import SqlImportError, Write, build_plan
from setron.features.db_import.service import SqlImportService, _read_target
from setron.features.storage.service import StorageService
from setron.features.management.validation import validate_snapshot_in
from setron.infrastructure.database import Database

from test_schema_mysql import schema_database  # noqa: F401
from test_sql_import_service import backup, bundle, current, seed, target  # noqa: F401


def _hpa_bundle(*, day=2):
    incoming = bundle(group="HPA", day=day)
    incoming["equipment_group"] = [(1, "HPA", "HPA", None, 0)]
    snapshot = list(incoming["equipment_snapshot"][0])
    snapshot[2] = date(2026, 11, day)
    incoming["equipment_snapshot"] = [tuple(snapshot)]
    return incoming


def _reject_conflicting_history(target, tmp_path):
    seed(target, bundle())
    path = backup(tmp_path, _hpa_bundle())
    before = current(target)
    for mode in ("add", "overwrite"):
        with pytest.raises(SqlImportError, match="날짜별 분류가 다릅니다"):
            target[2].preview(path, mode)
        assert current(target) == before


def test_preview_rejects_hpa_history_conflict_without_writing(target, tmp_path):
    _reject_conflicting_history(target, tmp_path)


@pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
def test_mysql_preview_rejects_hpa_history_conflict_without_writing(schema_database, tmp_path):
    metadata = MetaData()
    metadata.reflect(schema_database.engine)
    target = (schema_database.engine, metadata.tables,
              SqlImportService(lambda: Database.from_env(prefix="SETRON_TEST_DB_")))
    _reject_conflicting_history(target, tmp_path)


def test_overwrite_validates_replaced_children_after_pruning(target, tmp_path):
    seed(target, bundle())
    incoming = _hpa_bundle(day=1)
    service = target[2]
    service.apply(service.preview(backup(tmp_path, incoming), "overwrite"))
    with StorageService(Database(target[0])).transaction() as tx:
        validate_snapshot_in(tx, 1)
    rows = current(target)
    assert len(rows["equipment_version"]) == len(rows["equipment_pm"]) == 1


def test_same_hpa_classification_across_dates_remains_importable(target, tmp_path):
    seed(target, _hpa_bundle(day=1))
    service = target[2]
    path = backup(tmp_path, _hpa_bundle(day=2))
    service.apply(service.preview(path, "add"))
    assert len(current(target)["equipment_snapshot"]) == 2
    assert service.preview(path, "add").changes == 0


def test_unknown_pm_software_link_is_preserved_as_backup_evidence(target, tmp_path):
    incoming = bundle()
    incoming["equipment_pm"] = [(1, 1, None, b"PM1", 1, "O2", 1, None)]
    service = target[2]
    path = backup(tmp_path, incoming)
    service.apply(service.preview(path, "add"))
    assert current(target)["equipment_pm"][0]["equipment_version_id"] is None
    assert service.preview(path, "add").changes == 0


def test_unknown_snapshot_model_remains_backup_evidence(target, tmp_path):
    incoming = bundle()
    snapshot = list(incoming["equipment_snapshot"][0])
    snapshot[6] = None
    incoming["equipment_snapshot"] = [tuple(snapshot)]
    incoming["equipment_pm"] = []
    service = target[2]
    path = backup(tmp_path, incoming)
    service.apply(service.preview(path, "add"))
    assert current(target)["equipment_snapshot"][0]["group_id"] is None
    assert service.preview(path, "add").changes == 0


def test_same_category_pm_model_evidence_is_not_rejected_by_representative_model(target, tmp_path):
    incoming = bundle()
    incoming["equipment_group"].append((2, "O3", "OXIDE", None, 1))
    incoming["equipment_pm"].append((2, 1, 1, b"PM2", 2, "O3", 1, None))
    service = target[2]
    path = backup(tmp_path, incoming)
    service.apply(service.preview(path, "add"))
    assert {row["group_id"] for row in current(target)["equipment_pm"]} == {1, 2}
    assert service.preview(path, "add").changes == 0


def test_model_metadata_change_checks_unchanged_history(target, tmp_path):
    existing = bundle()
    existing["equipment_group"].append((2, "O3", "OXIDE", None, 1))
    existing["version_family"].append((2, 2, "O3", None))
    existing["equipment_snapshot"].append((2, 1, date(2026, 11, 2), "화성", "라인", "O3", 2,
                                           None, None, None, None, 1, None))
    existing["equipment_version"].append((2, 2, 2, b"v1", None, 1, None))
    existing["equipment_pm"].append((2, 2, 2, b"PM1", 2, "O3", 1, None))
    seed(target, existing)
    incoming = bundle()
    incoming["equipment_group"] = [(1, "O2", "OXIDE", None, 0)]
    # 현황 행 자체가 없는 설정 변경도 해당 모델을 쓰는 기존 날짜를 함께 검사한다.
    for table in ("equipment", "version_family", "equipment_snapshot", "equipment_version", "equipment_pm"):
        incoming[table] = []
    before = current(target)
    with pytest.raises(SqlImportError, match="날짜별 분류가 다릅니다"):
        target[2].preview(backup(tmp_path, incoming), "overwrite")
    assert current(target) == before


def _two_families():
    incoming = bundle()
    incoming["version_family"].append((2, 1, "OTHER", None))
    incoming["release_version"] = [
        (1, None, 1, b"v1", None, None, 1, "UNKNOWN", 1, None, "[]"),
        (2, None, 2, b"v2", None, None, 1, "UNKNOWN", 1, None, "[]"),
    ]
    incoming["equipment_version"] = [(1, 1, 1, b"v1", 1, 1, None)]
    return incoming


def test_malformed_source_cannot_link_software_to_another_family(target, tmp_path):
    incoming = _two_families()
    incoming["equipment_version"] = [(1, 1, 1, b"v1", 2, 1, None)]
    before = current(target)
    with pytest.raises(SqlImportError, match="적용 SW와 연결 릴리즈의 버전 계열"):
        target[2].preview(backup(tmp_path, incoming), "add")
    assert current(target) == before


@pytest.mark.parametrize("field", ["release_version_id", "start_release_version_id", "end_release_version_id"])
def test_malformed_risk_cannot_reference_another_family(target, tmp_path, field):
    incoming = _two_families()
    risk = [1, "DIRECT_VERSION", 1, None, None, None, None, None, "근거", 1, "{}", "UNRESOLVED"]
    risk[{"release_version_id": 5, "start_release_version_id": 6, "end_release_version_id": 7}[field]] = 2
    incoming["risk_rule"] = [tuple(risk)]
    with pytest.raises(SqlImportError, match="위험 정보와 연결 릴리즈의 버전 계열"):
        target[2].preview(backup(tmp_path, incoming), "add")
    assert not current(target)["risk_rule"]


def test_apply_checks_version_only_changes_before_any_write(target, tmp_path):
    seed(target, _two_families())
    service = target[2]
    plan = service.preview(backup(tmp_path, _two_families()), "add")
    before = current(target)
    version = {**before["equipment_version"][0], "release_version_id": 2}
    changed = replace(plan, writes=(Write("update", "equipment_version", {"equipment_version_id": 1}, version),))
    with pytest.raises(SqlImportError, match="적용 SW와 연결 릴리즈의 버전 계열"):
        service.apply(changed)
    assert current(target) == before


def test_apply_rechecks_final_hpa_history_before_any_write(target, tmp_path):
    seed(target, bundle())
    incoming = bundle(group="O3", day=2)
    snapshot = list(incoming["equipment_snapshot"][0])
    snapshot[2] = date(2026, 11, 2)
    incoming["equipment_snapshot"] = [tuple(snapshot)]
    service = target[2]
    plan = service.preview(backup(tmp_path, incoming), "add")
    altered = tuple(replace(write, row={**write.row, "category": "HPA", "analysis_enabled": 0})
                    if write.table == "equipment_group" else write for write in plan.writes)
    before = current(target)
    with pytest.raises(SqlImportError, match="날짜별 분류가 다릅니다"):
        service.apply(replace(plan, writes=altered))
    assert current(target) == before


def test_apply_checks_risk_only_changes_before_any_write(target, tmp_path):
    seed(target, _two_families())
    service = target[2]
    plan = service.preview(backup(tmp_path, _two_families()), "add")
    before = current(target)
    risk = dict(risk_rule_id=1, rule_type="DIRECT_VERSION", family_id=1, group_id=None, item_id=None,
                release_version_id=2, start_release_version_id=None, end_release_version_id=None,
                risk_reason="근거", is_enabled=1, target_reference="{}", mapping_status="UNRESOLVED")
    with pytest.raises(SqlImportError, match="위험 정보와 연결 릴리즈의 버전 계열"):
        service.apply(replace(plan, writes=(Write("insert", "risk_rule", {"risk_rule_id": 1}, risk),)))
    assert current(target) == before


def test_unrelated_existing_invalid_history_does_not_block_a_different_equipment(target, tmp_path):
    existing = _two_families()
    existing["equipment_version"] = [(1, 1, 1, b"v1", 2, 1, None)]
    seed(target, existing)
    incoming = bundle(code=b"EQ-2", group="O3")
    service = target[2]
    service.apply(service.preview(backup(tmp_path, incoming), "add"))
    assert len(current(target)["equipment"]) == 2


def test_preview_summary_keeps_the_actual_database_name(target, tmp_path):
    source = read_dump(backup(tmp_path, bundle()))
    with Database(target[0]).transaction() as session:
        repository, rows, next_ids = _read_target(session)
        plan = build_plan(source, rows, next_ids, repository, "add", "destination", "검증_DB")
    assert plan.summary.startswith("대상 DB 검증_DB ·")
    assert "대상 DB app_setting" not in plan.summary
