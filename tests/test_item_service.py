"""Item 포함 버전과 적용 설비 판정을 검증한다 (BE-014 완료 조건)."""

from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.item.schemas import (
    Application,
    Basis,
    Item,
    ItemState,
    TargetScope,
    VersionItem,
)
from setron.features.item.service import ItemCatalog, search_items
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases

M_C4, M_C4_PLUS, M_O2 = 3, 4, 1
F_C4, F_O2, F_EMPTY = 30, 10, 99

ITEM_A, ITEM_B, ITEM_C, ITEM_D, ITEM_E = 1, 2, 3, 4, 5
ITEMS = [
    Item(ITEM_A, 1, "PM 온도 보정 기능 추가"),
    Item(ITEM_B, 2, "Recipe 백업 자동화"),
    Item(ITEM_C, 3, "임시 로그 수집"),
    Item(ITEM_D, 4, "Plus 전용 이송 속도 개선"),
    Item(ITEM_E, None, "알람 문구 변경"),
]

V41, V42, V43, V44 = 301, 302, 303, 304


def c4_family(*extra):
    return FamilyReleases(F_C4, [
        ReleaseVersion(V41, F_C4, "v4.56.6.41", 1),
        ReleaseVersion(V42, F_C4, "v4.56.6.42", 2),
        ReleaseVersion(V43, F_C4, "v4.56.6.43", 3),
        ReleaseVersion(V44, F_C4, "v4.56.6.44", 4),
        *extra,
    ])


def row(release_id, item_id, state=ItemState.APPLIED, scope=TargetScope.ALL, groups=()):
    return VersionItem(release_id, item_id, state, scope, tuple(groups))


ROWS = [
    row(V42, ITEM_A),                                   # A: v42에서 추가
    row(V43, ITEM_B),                                   # B: v43에서 추가
    row(V42, ITEM_C),                                   # C: v42에서 추가, v44에서 제거
    row(V44, ITEM_C, ItemState.NOT_APPLIED),
    row(V43, ITEM_D, scope=TargetScope.LISTED, groups=[M_C4_PLUS]),  # D: C4 Plus 전용
    row(V42, ITEM_E, scope=TargetScope.UNKNOWN),        # E: 적용 모델 미확인
]


def families(*extra_c4):
    return {
        F_C4: c4_family(*extra_c4),
        F_O2: FamilyReleases(F_O2, [ReleaseVersion(101, F_O2, "v2.1", 1)]),
        F_EMPTY: FamilyReleases(F_EMPTY, []),
    }


@pytest.fixture
def catalog():
    return ItemCatalog(families(), ROWS)


def pm(version, group_id=M_C4, family_id=F_C4, name=None):
    return PmVersion(name, group_id, family_id, version)


def equipment(eid, *pms, site="화성", hpa=False):
    return EquipmentRecord(eid, f"EQ{eid}", date(2026, 10, 1), site, "P1D", "M-C4", tuple(pms), is_hpa=hpa)


def test_later_version_includes_earlier_items(catalog):
    """명세 예시: v2(42)에서 A, v3(43)에서 B가 추가되면 v3 설비에는 A와 B가 모두 적용된다."""
    a = catalog.pm_application(ITEM_A, pm("v4.56.6.43"))
    b = catalog.pm_application(ITEM_B, pm("v4.56.6.43"))

    assert (a.application, a.basis) == (Application.APPLIED, Basis.INHERITED)
    assert (b.application, b.basis) == (Application.APPLIED, Basis.EXPLICIT)


def test_version_before_item_was_added_is_not_applied(catalog):
    result = catalog.pm_application(ITEM_A, pm("v4.56.6.41"))

    assert (result.application, result.basis) == (Application.NOT_APPLIED, Basis.NOT_YET_ADDED)


def test_explicit_removal_stops_inheritance(catalog):
    assert catalog.pm_application(ITEM_C, pm("v4.56.6.43")).application is Application.APPLIED
    assert catalog.pm_application(ITEM_C, pm("v4.56.6.44")).application is Application.NOT_APPLIED


@pytest.mark.parametrize(
    ("group_id", "version", "expected"),
    [
        (M_C4, "v4.56.6.44", Application.NOT_TARGET),
        (M_C4_PLUS, "v4.56.6.44", Application.APPLIED),
        (M_C4_PLUS, "v4.56.6.42", Application.NOT_APPLIED),
    ],
    ids=["목록에 없는 원형 C4 → 대상 외", "C4 Plus 포함 버전 → 적용", "C4 Plus 추가 전 버전 → 미적용"],
)
def test_listed_scope_separates_not_target_from_not_applied(catalog, group_id, version, expected):
    assert catalog.pm_application(ITEM_D, pm(version, group_id)).application is expected


def test_unknown_scope_is_unknown_not_not_applied(catalog):
    result = catalog.pm_application(ITEM_E, pm("v4.56.6.44"))

    assert (result.application, result.basis) == (Application.UNKNOWN, Basis.SCOPE_UNKNOWN)


def test_item_of_other_family_is_not_target(catalog):
    result = catalog.pm_application(ITEM_A, pm("v2.1", M_O2, F_O2))

    assert (result.application, result.basis) == (Application.NOT_TARGET, Basis.NOT_IN_FAMILY)


def test_family_without_release_data_is_unknown(catalog):
    result = catalog.pm_application(ITEM_A, pm("v9.1", M_O2, F_EMPTY))

    assert (result.application, result.basis) == (Application.UNKNOWN, Basis.NO_RELEASE_DATA)


@pytest.mark.parametrize(
    ("version", "basis"),
    [("v4.56.6.45", Basis.NOT_REGISTERED), ("", Basis.MISSING_VERSION)],
    ids=["릴리즈 미등록", "버전 누락"],
)
def test_unmatched_version_is_unknown(catalog, version, basis):
    result = catalog.pm_application(ITEM_A, pm(version))

    assert (result.application, result.basis) == (Application.UNKNOWN, basis)


def test_version_with_unknown_order_is_unknown():
    catalog = ItemCatalog(families(ReleaseVersion(305, F_C4, "v4.56.6.43a1", None)), ROWS)

    result = catalog.pm_application(ITEM_A, pm("v4.56.6.43a1"))

    assert (result.application, result.basis) == (Application.UNKNOWN, Basis.ORDER_UNKNOWN)


def test_item_added_in_version_with_unknown_order_is_unknown():
    hotfix = ReleaseVersion(305, F_C4, "v4.56.6.42_d2", None)
    catalog = ItemCatalog(families(hotfix), [row(305, ITEM_A)])

    result = catalog.pm_application(ITEM_A, pm("v4.56.6.44"))

    assert result.application is Application.UNKNOWN


def test_item_versions_lists_inclusion_in_release_order(catalog):
    states = catalog.item_versions(ITEM_A)

    assert [(s.release.version_raw, s.state) for s in states] == [
        ("v4.56.6.41", ItemState.NOT_APPLIED),
        ("v4.56.6.42", ItemState.APPLIED),
        ("v4.56.6.43", ItemState.APPLIED),
        ("v4.56.6.44", ItemState.APPLIED),
    ]


def test_item_versions_is_empty_for_unknown_item(catalog):
    assert catalog.item_versions(999) == ()


@pytest.mark.parametrize(
    ("pms", "expected"),
    [
        ((pm("v4.56.6.43", name="PM1"), pm("v2.1", M_O2, F_O2, "PM2")), Application.APPLIED),
        ((pm("v4.56.6.43", name="PM1"), pm("v4.56.6.41", name="PM2")), Application.NOT_APPLIED),
        ((pm("v4.56.6.43", name="PM1"), pm(None, name="PM2")), Application.UNKNOWN),
        ((pm("v2.1", M_O2, F_O2),), Application.NOT_TARGET),
    ],
    ids=["대상 외 PM은 빼고 판정", "하나라도 미적용", "적용+누락 → 미확인", "모두 대상 외"],
)
def test_equipment_application_combines_pms(catalog, pms, expected):
    result = catalog.equipment_application(ITEM_A, equipment(1, *pms))

    assert result.application is expected
    assert len(result.pms) == len(pms)


def test_summary_counts_by_site_and_excludes_hpa(catalog):
    records = [
        equipment(1, pm("v4.56.6.43"), site="화성"),
        equipment(2, pm("v4.56.6.41"), site="화성"),
        equipment(3, pm("v4.56.6.44"), site="평택"),
        equipment(4, pm("v2.1", M_O2, F_O2), site="평택"),
        equipment(5, pm("v4.56.6.41"), site="평택", hpa=True),
    ]

    summary = catalog.summarize(ITEMS[0], records)

    assert summary.sites == ("평택", "화성")
    assert summary.counts("화성")[Application.APPLIED] == 1
    assert summary.counts("화성")[Application.NOT_APPLIED] == 1
    assert summary.counts("평택")[Application.APPLIED] == 1
    assert summary.counts("평택")[Application.NOT_TARGET] == 1
    assert sum(summary.counts().values()) == 4
    assert summary.hpa_excluded == 1


@pytest.mark.parametrize(
    ("query", "expected"),
    [("recipe", [ITEM_B]), ("3", [ITEM_C]), ("기능", [ITEM_A]), ("  ", []), ("없는 기능명", [])],
    ids=["대소문자 무시", "항목 번호", "내용 일부", "빈 검색어", "결과 없음"],
)
def test_search_items(query, expected):
    assert [i.item_id for i in search_items(ITEMS, query)] == expected


def test_catalog_rejects_row_of_unknown_release():
    with pytest.raises(ValueError, match="어느 계열에도 없습니다"):
        ItemCatalog(families(), [row(999, ITEM_A)])
