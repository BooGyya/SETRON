"""재가져오기 검토에서 당시 기준정보와 고정 PM 원문 변경을 빠짐없이 표시한다."""

from copy import deepcopy
from datetime import date

import pytest

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.excel_import.schemas import FIELD_LABELS, IssueLevel
from setron.features.excel_import.service import has_errors, validate, validate_batch

from test_import_validation import draft, family, reference, row


@pytest.mark.parametrize("field,old_value,new_value", [
    ("structure_type", "INLINE", "CLUSTER"),
    ("geometry_type", "5각", "6각"),
    ("modification_type", "CIP", "개조 확인"),
    ("modification_type", None, "CIP"),
    ("geometry_type", "5각", None),
])
def test_review_reports_snapshot_context_changes_without_mutation(field, old_value, new_value):
    """구조·형상·개조의 변경과 명시적인 공란도 기존값·새 값으로 확인할 수 있다."""
    old = SnapshotInput(
        equipment_code="E-1", base_date=date(2026, 11, 2), site="화성", model_name="혼합형 원문", batch_id=1,
        software=(SoftwareInput(family_id=1, version_raw="v1", pms=(
            PmInput(pm_name="PM1", group_id=1, model_name_raw="M-O2"),
        )),), **{field: old_value},
    )
    source = draft(row(**{field: new_value}))
    before = deepcopy((source, old))
    issues = validate(source, reference(family(), snapshots=(old,)))
    changes = [issue for issue in issues if issue.code == "STORED_VALUE_CHANGED" and issue.field == field]
    assert len(changes) == 1
    assert (changes[0].previous_value, changes[0].current_value) == (old_value, new_value)
    assert changes[0].level is IssueLevel.REVIEW
    assert not any(issue.code == "STORED_VALUE_IDENTICAL" for issue in issues)
    assert not has_errors(issues)
    assert (source, old) == before


def test_review_reports_model_when_existing_pm_raw_model_is_missing():
    """PM 모델 원문이 없으면 현황 대표 모델로 대신하지 않고 None에서의 변경을 표시한다."""
    old = SnapshotInput(
        equipment_code="E-1", base_date=date(2026, 11, 2), site="화성", model_name="M-O2", batch_id=1,
        software=(SoftwareInput(family_id=1, version_raw="v1", pms=(
            PmInput(pm_name="PM1", group_id=1, model_name_raw=None),
        )),),
    )
    issues = validate(draft(row()), reference(family(), snapshots=(old,)))
    change, = [issue for issue in issues if issue.code == "STORED_VALUE_CHANGED" and issue.field == "model_name"]
    assert (change.previous_value, change.current_value) == (None, "M-O2")
    assert not has_errors(issues)


@pytest.mark.parametrize("field,first,second", [
    ("structure_type", "INLINE", "CLUSTER"),
    ("geometry_type", "5각", "6각"),
    ("modification_type", "CIP", "다른 개조"),
])
def test_conflicting_snapshot_context_holds_all_pm_rows_and_sheets(field, first, second):
    """서로 다른 PM이라도 한 날짜의 공통 설비 정보가 상충하면 전체 묶음을 보류한다."""
    first_row = row(**{field: first})
    second_row = row(2, pm_name="PM2", model_name="M-O3", family_name="O3", **{field: second})
    same_sheet = validate(draft(first_row, second_row))
    separate_sheets = validate_batch((draft(first_row), draft(second_row, sheet="다른 현황")))
    for issues in (same_sheet, separate_sheets):
        assert has_errors(issues)
        assert any(issue.blocks_save and FIELD_LABELS[field] in issue.message for issue in issues)


@pytest.mark.parametrize("field,value", [
    ("structure_type", "INLINE"), ("geometry_type", "5각"), ("modification_type", "CIP"),
])
def test_missing_optional_context_with_known_value_keeps_existing_policy(field, value):
    """공란과 명시값의 혼합은 상충으로 만들지 않으며 PM별 다른 모델을 허용한다."""
    source = draft(row(**{field: None}),
                   row(2, pm_name="PM2", model_name="M-O3", family_name="O3", **{field: value}))
    assert not has_errors(validate(source))
