"""BE-010에서 등록 상태와 목표 N의 비교 가능 상태가 독립적인지 검증한다."""

import pytest

from setron.features.version.schemas import CompareReason, CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases, compare_version
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ReleaseInput

from test_release_revision import observation, revision_storage


@pytest.mark.parametrize("target_id, reason", [(None, CompareReason.TARGET_NOT_SET), (99, CompareReason.TARGET_INVALID)])
@pytest.mark.parametrize("raw, linked, expected", [("v1", None, True), ("v1 (확정 표기)", 1, True),
                                                   ("미등록 원문", None, False), ("v1", 999, False)])
def test_registration_survives_missing_or_invalid_target(target_id, reason, raw, linked, expected):
    """N이 없어도 등록 정보를 유지하며 잘못된 명시 ID를 원문으로 대체하지 않는지 확인한다.

    param
    - target_id, reason: 목표 N의 누락·무효 입력과 기존 판정 사유.
    - raw, linked, expected: 설치 원문·명시 대응 ID와 등록 여부 기대값.
    """
    family = FamilyReleases(1, (ReleaseVersion(1, 1, "v1", 1),))
    result = compare_version(family, raw, target_id, release_version_id=linked)
    assert result.registered is expected
    assert result.version_raw == raw
    assert (result.status, result.reason) == (CompareStatus.UNDETERMINED, reason)


def test_registration_refresh_reports_registered_even_without_target(revision_storage):
    """목표를 고르기 전 릴리즈 등록도 과거 설비의 등록 상태를 즉시 갱신하는지 확인한다.

    param
    - revision_storage: 원본 보존과 실제 트랜잭션을 확인하는 임시 업무 DB.
    """
    observation(revision_storage, versions=((1, 1, "v1", None),))
    result = ReleaseRevisionService(revision_storage).register_release(
        ReleaseInput(family_id=1, version_raw="v1", version_order=1))
    comparison = result.refresh.dates[0].summary.equipment[0].families[0].pms[0].comparison
    assert comparison.registered
    assert comparison.release.release_version_id == result.value.release.current["release_version_id"]
    assert comparison.reason is CompareReason.TARGET_NOT_SET
