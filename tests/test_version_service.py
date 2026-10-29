"""같은 계열 안의 버전 비교와 N 대비 단계 계산을 검증한다 (BE-003 완료 조건)."""

import pytest

from setron.features.version.schemas import CompareReason, CompareStatus, NotationDifference, ReleaseVersion
from setron.features.version.service import FamilyReleases, compare_version, sort_releases

C4 = 1
C3_LEGACY = 2


def c4_releases():
    """정규 41→42→42_d2(핫픽스)→43→44 순서. 순서는 릴리즈 자료에서 확인한 값이라고 가정한다."""
    return [
        ReleaseVersion(10, C4, "v4.56.6.41", 1),
        ReleaseVersion(11, C4, "v4.56.6.42", 2),
        ReleaseVersion(12, C4, "v4.56.6.42_d2", 3),
        ReleaseVersion(13, C4, "v4.56.6.43", 4),
        ReleaseVersion(14, C4, "v4.56.6.44", 5),
    ]


@pytest.fixture
def c4():
    return FamilyReleases(C4, c4_releases())


@pytest.mark.parametrize(
    ("version_raw", "label", "steps"),
    [
        ("v4.56.6.43", "N-1", 1),
        ("v4.56.6.42_d2", "N-2", 2),
        ("v4.56.6.42", "N-3", 3),
        ("v4.56.6.41", "N-4", 4),
    ],
    ids=["한 단계 낮음", "핫픽스도 한 단계", "핫픽스를 건너뛰지 않음", "가장 오래된 버전"],
)
def test_below_target_counts_steps_including_hotfix(c4, version_raw, label, steps):
    result = compare_version(c4, version_raw, target_id=14)

    assert result.status is CompareStatus.BELOW
    assert result.steps_below == steps
    assert result.label == label
    assert result.registered


@pytest.mark.parametrize("version_raw", ["v4.56.6.43", "v4.56.6.44"], ids=["N과 같음", "N보다 높음"])
def test_target_and_higher_are_all_at_or_above(c4, version_raw):
    result = compare_version(c4, version_raw, target_id=13)

    assert result.status is CompareStatus.AT_OR_ABOVE
    assert result.label == "N 이상"
    assert result.steps_below is None


def test_unregistered_version_is_undetermined_and_keeps_raw(c4):
    result = compare_version(c4, "v4.56.6.45", target_id=14)

    assert result.status is CompareStatus.UNDETERMINED
    assert result.reason is CompareReason.NOT_REGISTERED
    assert result.label == "판단 불가"
    assert not result.registered
    assert result.version_raw == "v4.56.6.45"


def test_suffix_is_not_matched_to_base_version(c4):
    """`_DFB`는 별도 파생 버전이다. 기본 버전 v4.56.6.44가 있어도 같은 버전으로 연결하지 않는다."""
    result = compare_version(c4, "v4.56.6.44_DFB", target_id=14)

    assert result.reason is CompareReason.NOT_REGISTERED


@pytest.mark.parametrize("version_raw", [None, "", "   "], ids=["None", "빈 문자열", "공백"])
def test_missing_version_is_undetermined(c4, version_raw):
    result = compare_version(c4, version_raw, target_id=14)

    assert result.status is CompareStatus.UNDETERMINED
    assert result.reason is CompareReason.MISSING_VERSION


def test_release_from_other_family_is_not_compared(c4):
    result = compare_version(c4, "v3.52.1.10", target_id=14, release_version_id=99)

    assert result.status is CompareStatus.UNDETERMINED
    assert result.reason is CompareReason.OTHER_FAMILY


def test_stored_release_id_is_used_before_raw_match(c4):
    result = compare_version(c4, "v4.56.6.43 (H)", target_id=14, release_version_id=13)

    assert result.label == "N-1"
    assert result.version_raw == "v4.56.6.43 (H)"


def test_release_with_unknown_order_is_undetermined():
    family = FamilyReleases(C4, [*c4_releases(), ReleaseVersion(15, C4, "v4.56.6.43a1", None)])

    result = compare_version(family, "v4.56.6.43a1", target_id=14)

    assert result.status is CompareStatus.UNDETERMINED
    assert result.reason is CompareReason.ORDER_UNKNOWN
    assert result.registered


def test_below_without_steps_when_family_has_unordered_release():
    """순서를 모르는 핫픽스가 사이에 있을 수 있으므로 N 미만만 확정하고 단계 수는 정하지 않는다."""
    family = FamilyReleases(C4, [*c4_releases(), ReleaseVersion(15, C4, "v4.56.6.43a1", None)])

    result = compare_version(family, "v4.56.6.42", target_id=14)

    assert result.status is CompareStatus.BELOW
    assert result.steps_below is None
    assert result.label == "N 미만"
    assert result.reason is CompareReason.ORDER_UNKNOWN


def test_duplicated_order_makes_comparison_undetermined():
    family = FamilyReleases(C4, [*c4_releases(), ReleaseVersion(15, C4, "v4.56.6.43_n4", 4)])

    same_position = compare_version(family, "v4.56.6.43_n4", target_id=14)
    across = compare_version(family, "v4.56.6.42", target_id=14)

    assert same_position.status is CompareStatus.UNDETERMINED
    assert same_position.reason is CompareReason.ORDER_CONFLICT
    assert across.status is CompareStatus.BELOW
    assert across.steps_below is None
    assert across.reason is CompareReason.ORDER_CONFLICT


@pytest.mark.parametrize("target_id", [99, 15], ids=["계열에 없는 N", "순서를 모르는 N"])
def test_invalid_target_asks_to_reselect(target_id):
    family = FamilyReleases(C4, [*c4_releases(), ReleaseVersion(15, C4, "v4.56.6.43a1", None)])

    result = compare_version(family, "v4.56.6.41", target_id=target_id)

    assert result.status is CompareStatus.UNDETERMINED
    assert result.reason is CompareReason.TARGET_INVALID
    assert "다시 선택" in result.message


def test_confirmed_alias_links_to_release_and_keeps_raw():
    kctc = 3
    releases = [ReleaseVersion(30, kctc, "v999.3.6", 1), ReleaseVersion(31, kctc, "v999.3.7", 2)]
    family = FamilyReleases(kctc, releases, aliases={"v999.3_7": "v999.3.7"})

    result = compare_version(family, "v999.3_7", target_id=31)

    assert result.status is CompareStatus.AT_OR_ABOVE
    assert result.release.version_raw == "v999.3.7"
    assert result.version_raw == "v999.3_7"


def test_without_alias_dot_and_underscore_are_not_replaced():
    kctc = 3
    family = FamilyReleases(kctc, [ReleaseVersion(31, kctc, "v999.3.7", 1)])

    assert family.match("v999.3_7") is None


def test_sort_releases_orders_by_confirmed_order_and_reports_problems():
    releases = [
        ReleaseVersion(13, C4, "v4.56.6.43", 4),
        ReleaseVersion(15, C4, "v4.56.6.43a1", None),
        ReleaseVersion(11, C4, "v4.56.6.42", 2),
        ReleaseVersion(16, C4, "v4.56.6.43_n4", 4),
    ]

    result = sort_releases(releases)

    assert [r.version_raw for r in result.ordered] == ["v4.56.6.42", "v4.56.6.43", "v4.56.6.43_n4"]
    assert [r.version_raw for r in result.unordered] == ["v4.56.6.43a1"]
    assert result.duplicated_orders == (4,)


def test_family_rejects_releases_from_other_family():
    with pytest.raises(ValueError, match="섞여"):
        FamilyReleases(C4, [*c4_releases(), ReleaseVersion(20, C3_LEGACY, "v3.52.1.10", 1)])


def test_family_rejects_alias_to_unknown_release():
    with pytest.raises(ValueError, match="없습니다"):
        FamilyReleases(C4, c4_releases(), aliases={"v4.56.6.45x": "v4.56.6.45"})


# --- 표기만 다른 릴리즈 후보 ------------------------------------------------

C3_LEGACY_RELEASES = [
    ReleaseVersion(40, C3_LEGACY, "v1.1.1.6B_6", 1),
    ReleaseVersion(41, C3_LEGACY, "v1.1.1.6B_6+Hotfix", 2),
]


def candidate_view(result):
    return [(c.release.version_raw, c.differences) for c in result]


def test_hotfix_with_spaces_is_suggested_but_not_linked():
    """멘토 답변(10-29): 릴리즈 노트는 'v1.1.1.6B_6+Hotfix', 화성 현황표는 'v1.1.1.6B_6 + Hotfix'."""
    family = FamilyReleases(C3_LEGACY, C3_LEGACY_RELEASES)

    assert family.match("v1.1.1.6B_6 + Hotfix") is None
    assert candidate_view(family.candidates("v1.1.1.6B_6 + Hotfix")) == [
        ("v1.1.1.6B_6+Hotfix", (NotationDifference.WHITESPACE,)),
    ]
    assert compare_version(family, "v1.1.1.6B_6 + Hotfix", target_id=41).reason is CompareReason.NOT_REGISTERED


def test_chosen_candidate_is_used_through_release_id():
    """사용자가 후보를 고르면 그 릴리즈 ID를 저장하고, 비교는 저장된 ID로 한다."""
    family = FamilyReleases(C3_LEGACY, C3_LEGACY_RELEASES)
    chosen = family.candidates("v1.1.1.6B_6 + Hotfix")[0].release

    result = compare_version(family, "v1.1.1.6B_6 + Hotfix", target_id=41,
                             release_version_id=chosen.release_version_id)

    assert result.status is CompareStatus.AT_OR_ABOVE
    assert result.version_raw == "v1.1.1.6B_6 + Hotfix"


@pytest.mark.parametrize(
    ("raw", "release_raw", "differences"),
    [
        ("v3.52_13 (H)", "v3.52_13", (NotationDifference.SITE_MARK,)),
        ("V4.56.6.42", "v4.56.6.42", (NotationDifference.CASE,)),
        ("4.56.6.42", "v4.56.6.42", (NotationDifference.PREFIX,)),
        ("v999.3_7", "v999.3.7", (NotationDifference.SEPARATOR,)),
        ("V999.3_7 (H)", "v999.3.7", (NotationDifference.SITE_MARK, NotationDifference.CASE,
                                      NotationDifference.SEPARATOR)),
    ],
    ids=["(H) 표식", "대소문자", "앞의 v", "점·밑줄", "여러 차이"],
)
def test_candidate_reports_notation_differences(raw, release_raw, differences):
    family = FamilyReleases(C4, [ReleaseVersion(1, C4, release_raw, 1)])

    result = family.candidates(raw)

    assert candidate_view(result) == [(release_raw, differences)]
    assert result[0].description.endswith("차이")


@pytest.mark.parametrize(
    "raw",
    ["v1.1.1.6B_6_c", "v1.1.1.6B_6", "", None],
    ids=["릴리즈 노트에 없는 실제 버전", "원문이 바로 연결됨", "빈 문자열", "None"],
)
def test_no_candidates(raw):
    family = FamilyReleases(C3_LEGACY, C3_LEGACY_RELEASES)

    assert family.candidates(raw) == ()


def test_candidates_put_separator_difference_last():
    family = FamilyReleases(C4, [
        ReleaseVersion(1, C4, "v4.56.6_42", 1),
        ReleaseVersion(2, C4, "v4.56.6.42 ", 2),
    ])

    result = family.candidates("V4.56.6.42")

    assert [c.release.release_version_id for c in result] == [2, 1]
