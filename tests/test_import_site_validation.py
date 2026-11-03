"""사업장은 날짜와 관계없이 같은 호기의 고정 소속임을 가져오기 검토에서 검사한다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.excel_import.reference import ReferenceData
from setron.features.excel_import.schemas import ImportBlockedError, IssueLevel
from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.service import ImportValidationService, has_errors, validate, validate_batch

from test_import_validation import draft, family, reference, row
from test_import_validation_repository import database


def snapshot(site="평택", base_date=date(2026, 11, 1)):
    """사업장 검증에 쓰는 한 날짜의 합성 기존 현황을 만든다."""
    return SnapshotInput(
        equipment_code="E-1", base_date=base_date, site=site, model_name="M-O2", batch_id=1,
        software=(SoftwareInput(family_id=1, version_raw="v1", pms=(
            PmInput(pm_name="PM1", group_id=1, model_name_raw="M-O2"),
        )),),
    )


@pytest.mark.parametrize("stored_date", [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)])
def test_snapshot_only_reference_blocks_other_site_for_any_date(stored_date):
    """정확한 날짜 현황만 주입한 호출도 기존 호기의 다른 사업장을 변경으로 승인하지 않는다."""
    old = snapshot(base_date=stored_date)
    source = draft(row(site="화성"))
    before = deepcopy((source, old))
    issues = validate(source, reference(family(), snapshots=(old,)))
    problem, = [issue for issue in issues if issue.code == "SITE_CONFLICT"]
    assert problem.level is IssueLevel.ERROR and problem.field == "site"
    assert (problem.previous_value, problem.current_value) == ("평택", "화성")
    assert not any(issue.code == "STORED_VALUE_CHANGED" and issue.field == "site" for issue in issues)
    assert (source, old) == before


def test_same_site_is_allowed_without_filling_missing_date():
    """사업장 고정 소속 검사에 과거 정보를 사용해도 해당 날짜의 SW 현황을 보충하지 않는다."""
    ref = reference(family(), snapshots=(snapshot(),))
    issues = validate(draft(row(site="평택", version_raw="v2")), ref)
    assert not has_errors(issues)
    assert not any(issue.code in {"STORED_VALUE_CHANGED", "STORED_VALUE_IDENTICAL"} for issue in issues)


@pytest.mark.parametrize("current_site", ["평택", "화성"])
def test_conflicting_historical_sites_always_hold_the_batch(current_site):
    """기존 여러 사업장 기록 중 하나와 같아도 고정 소속 충돌을 먼저 해결해야 한다."""
    ref = replace(reference(family()), equipment_sites={"E-1": frozenset({"평택", "화성"})})
    issues = validate(draft(row(site=current_site)), ref)
    problem, = [issue for issue in issues if issue.code == "SITE_CONFLICT"]
    assert problem.blocks_save and set(problem.previous_value) == {"평택", "화성"}


@pytest.mark.parametrize("separate_sheets", [False, True])
def test_new_equipment_cannot_use_different_sites_across_dates(separate_sheets):
    """아직 저장되지 않은 호기도 날짜·시트와 관계없이 한 사업장만 지정할 수 있다."""
    first = row(site="평택", base_date="2026-11-01")
    second = row(2, site="화성", base_date="2026-11-02")
    drafts = ((draft(first), draft(second, sheet="다른 날짜")) if separate_sheets else (draft(first, second),))
    issues = validate_batch(drafts)
    problems = [issue for issue in issues if issue.code == "SITE_CONFLICT"]
    assert {issue.row_id for issue in problems} == {1, 2}
    assert all(issue.blocks_save and issue.field == "site" for issue in problems)
    assert {issue.sheet_name for issue in problems} == ({"현황", "다른 날짜"} if separate_sheets else {"현황"})


def test_missing_site_still_uses_required_value_error():
    """사업장이 공란이면 기존 소속으로 채우지 않고 필수값 오류로 보류한다."""
    ref = replace(reference(family()), equipment_sites={"E-1": frozenset({"평택"})})
    issues = validate(draft(row(site=None)), ref)
    assert has_errors(issues)
    assert any(issue.blocks_save and issue.field == "site" for issue in issues)
    assert not any(issue.code == "SITE_CONFLICT" for issue in issues)


@pytest.mark.parametrize("separate_sheets", [False, True])
def test_equipment_workbook_cannot_mix_sites_even_for_different_equipment(separate_sheets):
    """서로 다른 호기여도 현황 파일 하나에 여러 사업장이 섞이면 오류로 보류한다."""
    first = row(equipment_code="E-1", site="평택")
    second = row(2, equipment_code="E-2", site="화성")
    drafts = ((draft(first), draft(second, sheet="다른 현황")) if separate_sheets else (draft(first, second),))
    issues = validate_batch(drafts)
    problems = [issue for issue in issues if issue.code == "SITE_CONFLICT"]
    assert len(problems) == 2 and all(issue.blocks_save for issue in problems)


def test_different_equipment_workbooks_can_each_have_their_own_site():
    """사업장이 다른 별개 현황 파일을 같은 검증 묶음에 넣을 수 있다."""
    issues = validate_batch((draft(row(site="평택"), book="평택.xlsx"),
                             draft(row(equipment_code="E-2", site="화성"), book="화성.xlsx")))
    assert not has_errors(issues)


def test_release_notes_are_not_subject_to_equipment_workbook_site_rules():
    """릴리즈 노트는 설비 현황의 사업장별 파일 규칙으로 차단하지 않는다."""
    issues = validate_batch((draft(row(site="평택", version_raw="v1"), kind=ExtractKind.RELEASE_NOTE),
                             draft(row(site="화성", version_raw="v2"), kind=ExtractKind.RELEASE_NOTE, sheet="O3")))
    assert not any(issue.code == "SITE_CONFLICT" for issue in issues)


def test_reference_site_mapping_has_independent_empty_defaults():
    """기존 호출에 새 자료가 필수화되거나 인스턴스 간 기본 사전이 공유되지 않는다."""
    first, second = ReferenceData(frozenset()), ReferenceData(frozenset())
    assert first.equipment_sites == second.equipment_sites == {}
    assert first.equipment_sites is not second.equipment_sites


def test_repository_reads_all_dates_for_site_but_keeps_exact_snapshot_query(database):
    """새 날짜 검사에서도 고정 사업장을 읽고 날짜 현황은 빈 값으로 유지한다."""
    storage, _, _, commands = database
    source = draft(row(site="화성", base_date="2026-11-03"))
    service = ImportValidationService(storage)
    with storage.transaction() as tx:
        ref = service.repository.load(tx, (source,))
    assert ref.equipment_sites == {"E-1": frozenset({"평택"})}
    assert ref.snapshots == ()
    problems = [issue for issue in validate(source, ref) if issue.code == "SITE_CONFLICT"]
    assert len(problems) == 1 and problems[0].blocks_save
    with pytest.raises(ImportBlockedError):
        service.prepare((source,), "site-conflict")
    assert commands and all(command == "SELECT" for command in commands)


def test_repository_does_not_hide_historical_multiple_site_conflict(database):
    """모든 날짜의 사업장 불일치를 읽고 입력 사업장이 그중 하나여도 차단한다."""
    storage, engine, tables, _ = database
    with engine.begin() as connection:
        connection.execute(tables["equipment_snapshot"].insert(), dict(
            snapshot_id=2, equipment_id=1, base_date=date(2026, 11, 1), site="화성", model_name="M-O2", batch_id=1,
        ))
    service = ImportValidationService(storage)
    source = draft(row(site="평택", base_date="2026-11-03"))
    issues = service.validate((source,))
    problem, = [issue for issue in issues if issue.code == "SITE_CONFLICT"]
    assert problem.blocks_save and set(problem.previous_value) == {"평택", "화성"}
