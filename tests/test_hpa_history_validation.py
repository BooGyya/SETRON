"""HPA 분류 이력 충돌을 원본 위치와 함께 저장 전 검토에서 차단한다."""

import pytest

from setron.features.excel_import.schemas import ImportBlockedError
from setron.features.excel_import.service import ImportValidationService

from test_hpa_merge_safety import HPA, NORMAL, _counts, hpa_storage  # noqa: F401
from test_import_validation import draft, row
from test_snapshot_reimport import save, snapshot, storage  # noqa: F401


@pytest.mark.parametrize("first_hpa", [False, True])
def test_new_equipment_with_conflicting_dates_is_held_before_request_creation(hpa_storage, first_hpa):
    """신규 호기도 두 날짜의 HPA 구분이 다르면 모든 시트의 검토에 원인을 표시한다."""
    first, second = (HPA, NORMAL) if first_hpa else (NORMAL, HPA)
    earlier = draft(row(base_date="2026-11-02", **first), sheet="이전 날짜")
    later = draft(row(base_date="2026-11-03", **second), sheet="새 날짜")
    validator = ImportValidationService(hpa_storage)
    before = _counts(hpa_storage)
    issues = [issue for issue in validator.validate((earlier, later)) if issue.code == "HPA_HISTORY_CONFLICT"]
    assert len(issues) == 2 and all(issue.blocks_save and issue.excel_position is not None for issue in issues)
    assert {issue.sheet_name for issue in issues} == {"이전 날짜", "새 날짜"}

    with pytest.raises(ImportBlockedError, match="원본"):
        validator.prepare((earlier, later), "new-conflicting-dates")

    assert _counts(hpa_storage) == before


def test_reimporting_two_dates_does_not_hide_the_original_classification(hpa_storage):
    """두 날짜를 함께 다시 가져와도 기존의 다른 날짜 분류를 덮어 검증하지 않는다."""
    storage = hpa_storage
    for day in ("2026-11-02", "2026-11-03"):
        save(storage, row(base_date=day, **HPA), request_id=f"initial-{day}")
    before = _counts(storage)
    earlier, later = (draft(row(base_date=day, **NORMAL), sheet=day) for day in ("2026-11-02", "2026-11-03"))
    validator = ImportValidationService(storage)

    with pytest.raises(ImportBlockedError, match="HPA"):
        validator.prepare((earlier, later), "both-dates-reclassified")

    assert _counts(storage) == before
    assert snapshot(storage).software[0].version_raw == HPA["version_raw"]


def test_same_day_full_correction_is_not_a_different_date_conflict(hpa_storage):
    """다른 날짜 기록이 없는 경우 같은 날짜의 전체 PM 원본 정정은 검토할 수 있다."""
    storage = hpa_storage
    save(storage, row(1, **HPA), row(2, pm_name="PM2", **HPA), request_id="same-day-initial")
    corrected = draft(row(1, **NORMAL), row(2, pm_name="PM2", **NORMAL))

    assert not any(issue.code == "HPA_HISTORY_CONFLICT" for issue in ImportValidationService(storage).validate((corrected,)))
    save(storage, *corrected.rows, request_id="same-day-corrected")
    assert all(pm.group_id == 1 for software in snapshot(storage).software for pm in software.pms)


def test_registered_analysis_exclusion_uses_the_same_kind_as_stored_history(hpa_storage):
    """등록 모델의 분석 제외 설정 때문에 같은 종류의 다음 날짜 입력을 오차단하지 않는다."""
    storage = hpa_storage
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"analysis_enabled": False})
    save(storage, row(**NORMAL), request_id="excluded-model-original")
    later = draft(row(base_date="2026-11-03", **NORMAL))
    validator = ImportValidationService(storage)

    assert not any(issue.code == "HPA_HISTORY_CONFLICT" for issue in validator.validate((later,)))
    save(storage, *later.rows, request_id="excluded-model-later")
    assert snapshot(storage, base="2026-11-03") is not None
