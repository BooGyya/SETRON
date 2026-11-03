"""BE-024: 합성 증강 자료를 실제 MySQL 5.6.19에 저장·재조회하고 HPA 분석 제외를 검증한다."""

from dataclasses import replace
from datetime import date
import os

import pytest

from setron.features.aggregation.service import summarize
from setron.features.analysis.schemas import AnalysisInput
from setron.features.analysis.service import build_facts
from setron.features.equipment.read import read_equipment_records
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import ImportValidationService, edit_value, has_errors
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import StorageValidationError
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.database_source import load_dashboard_source
from setron.ui.query_state import QueryState
from setron.ui.equipment_data import equipment_data

from test_hpa_import import hpa_draft
from test_import_save_mysql import count, storage  # noqa: F401  기존 시연 카탈로그 fixture 재사용
from test_schema_mysql import schema_database  # noqa: F401  빈 테스트 DB 확인·생성·정리 재사용


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
DAY = date(2026, 11, 3)


def test_hpa_round_trip_keeps_exact_values_sources_and_excludes_common_analysis(storage):  # noqa: F811
    """일반 설비와 같은 묶음으로 저장한 HPA의 버전·선택 공란·원본을 재조회한다.

    param
    - storage: 빈 전용 MySQL 테스트 DB에 시연 카탈로그를 적용한 저장 서비스.
    """
    normal = hpa_draft(code="TEST-O2-024", version="v2", model="M-O2", group="Oxide", family_name="O2")
    hpa = hpa_draft()
    drafts = (normal, hpa)
    receipts = DatabaseImportSaver(storage).save_many(ImportValidationService(storage).prepare(drafts, "hpa-round-trip"))
    service = EquipmentService(storage)
    snapshots = tuple(service.get_snapshot(code, DAY) for code in ("TEST-O2-024", "TEST-HPA-024"))
    saved = snapshots[1]
    assert saved.equipment_code == "TEST-HPA-024" and saved.model_name == "HPA" and saved.site == "화성"
    assert saved.production_area is None and saved.software[0].version_raw == "vHPA.2_d5 "
    assert saved.software[0].release_version_id is None
    assert saved.software[0].source_reference["version_raw"] == {"sheet": "HPA 검증", "cells": ["H6"]}
    with storage.transaction() as tx:
        group = tx.get("equipment_group", {"group_id": saved.group_id})
        original = tx.get("import_batch", {"batch_id": receipts[1].batch_id})["raw_data"]["HPA 검증"]
    assert group["category"] == "HPA" and not group["analysis_enabled"]
    assert original["H6"] == "vHPA.2_d5 " and original["I6"] is None

    records = read_equipment_records(snapshots, (saved.group_id,))
    fid = snapshots[0].software[0].family_id
    families = {fid: FamilyReleases(fid, (ReleaseVersion(1, fid, "v1", 1), ReleaseVersion(2, fid, "v2", 2)))}
    targets = {(snapshots[0].group_id, fid): 2}
    summary = summarize(records, families, targets)
    assert (summary.equipment_count, summary.hpa_excluded, summary.compliant, summary.undetermined) == (1, 1, 1, 0)
    assert summary.rate == 1 and summary.complete
    facts = build_facts(AnalysisInput(records=records, families=families, targets=targets))
    assert facts["전체 현황"]["설비 수(HPA 제외)"] == 1
    assert [entry["계열"] for entry in facts["버전 분포"]] == [f"계열 {fid}"]
    source = load_dashboard_source(storage)
    dashboard = source.snapshot(QueryState(source.catalog).apply())
    assert dashboard.summary.equipment_count == 1 and dashboard.summary.hpa_excluded == 1
    assert any(record.equipment_code == saved.equipment_code and record.is_hpa for record in source.records)


@pytest.mark.parametrize("field", ["equipment_code", "version_raw"])
def test_hpa_missing_required_value_holds_all_sheets_then_saves_after_correction(storage, field):  # noqa: F811
    """빈 필수값의 파일·시트·행·열을 안내하고 보완 전 정상 시트까지 쓰지 않는다.

    param
    - storage: 전용 MySQL 테스트 DB의 저장 서비스.
    - field: 공란 검증 후 사용자가 보완할 호기 또는 SW 항목.
    """
    suffix = "CODE" if field == "equipment_code" else "SW"
    normal_code, hpa_code = f"TEST-GOOD-{suffix}-024", f"TEST-HPA-{suffix}-024"
    normal = hpa_draft(code=normal_code, version="v2", model="M-O2", group="Oxide", family_name="O2")
    broken = hpa_draft(code=None if field == "equipment_code" else hpa_code,
                       version=None if field == "version_raw" else "vHPA.2")
    validator = ImportValidationService(storage)
    before = count(storage, "import_batch")
    issues = validator.validate((normal, broken))
    missing = [issue for issue in issues if issue.blocks_save and issue.field == field]
    assert missing
    assert all(issue.workbook_name == "BE-024_합성_증강자료.xlsx" and issue.sheet_name == "HPA 검증" for issue in missing)
    assert all(issue.excel_position == (6, 6 if field == "equipment_code" else 8) for issue in missing)
    with pytest.raises(ImportBlockedError):
        validator.prepare((normal, broken), f"hpa-blocked-{suffix}")
    assert count(storage, "import_batch") == before
    assert EquipmentService(storage).get_snapshot(normal_code, DAY) is None
    corrected = edit_value(broken, 1, field, hpa_code if field == "equipment_code" else "vHPA.2")
    assert not has_errors(validator.validate((normal, corrected)))
    DatabaseImportSaver(storage).save_many(validator.prepare((normal, corrected), f"hpa-corrected-{suffix}"))
    saved = EquipmentService(storage).get_snapshot(hpa_code, DAY)
    assert saved is not None and saved.software[0].version_raw == "vHPA.2"
    assert saved.production_area is None
    assert EquipmentService(storage).get_snapshot(normal_code, DAY) is not None


def test_forged_hpa_save_request_is_revalidated_and_does_not_partially_save(storage):  # noqa: F811
    """검토 뒤 SW를 비운 요청도 저장 경계에서 정상 시트와 함께 거부한다.

    param
    - storage: 전용 MySQL 테스트 DB의 저장 서비스.
    """
    drafts = (hpa_draft(code="TEST-GOOD-FORGED-024", model="M-O2", group="Oxide", family_name="O2"),
              hpa_draft(code="TEST-HPA-FORGED-024"))
    requests = ImportValidationService(storage).prepare(drafts, "hpa-forged")
    last = requests[1]
    bad_row = replace(last.rows[0], values={**last.rows[0].values, "version_raw": None})
    forged = (requests[0], replace(last, rows=(bad_row,)))
    before = count(storage, "import_batch")
    with pytest.raises(StorageValidationError):
        DatabaseImportSaver(storage).save_many(forged)
    assert count(storage, "import_batch") == before
    assert EquipmentService(storage).get_snapshot("TEST-GOOD-FORGED-024", DAY) is None


def test_newer_hpa_observation_is_available_without_changing_analysis_day(storage):  # noqa: F811
    """실제 DB의 새 날짜 HPA가 일반 분석을 밀어내지 않고 최신 원문으로 상세 조회된다.

    param
    - storage: 전용 MySQL 테스트 DB의 저장 서비스.
    """
    service, validator, saver = EquipmentService(storage), ImportValidationService(storage), DatabaseImportSaver(storage)
    code = "TEST-HPA-LATEST-024"
    old = hpa_draft(code=code, version="vHPA.OLD")
    saver.save_many(validator.prepare((old,), "hpa-old-date"))
    baseline_source = load_dashboard_source(storage)
    baseline = baseline_source.snapshot(QueryState(baseline_source.catalog).apply())
    latest = date(2026, 11, 4)
    new = hpa_draft(code=code, version="vHPA.LATEST ", base_date=latest.isoformat())
    saver.save_many(validator.prepare((new,), "hpa-new-date"))
    source = load_dashboard_source(storage)
    snapshot = source.snapshot(QueryState(source.catalog).apply())
    assert snapshot.base_date == baseline.base_date == DAY
    assert (snapshot.summary.equipment_count, snapshot.summary.rate) == (
        baseline.summary.equipment_count, baseline.summary.rate)
    lookup = next(record for record in snapshot.hpa_lookup_records if record.equipment_code == code)
    assert lookup.base_date == latest and lookup.pms[0].version_raw == "vHPA.LATEST "
    detail = next(item for item in equipment_data(snapshot, source).details if item.record.equipment_code == code)
    assert detail.record.base_date == latest and detail.record.pms[0].version_raw == "vHPA.LATEST "
    assert detail.status is None
    assert service.get_snapshot(code, DAY).software[0].version_raw == "vHPA.OLD"
