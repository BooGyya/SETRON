"""아이템 매니저 AI 답변 카드(화면 08-1) 요약을 검증한다 (BE-014): 포함 버전과 적용 현황."""

from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.item.schemas import ApplicationCount
from setron.features.item.service import ItemCatalog
from setron.features.version.schemas import ReleaseVersion
from test_item_service import (F_C4, F_O2, ITEM_A, ITEM_C, ITEM_D, ITEMS, M_C4, M_C4_PLUS, M_O2, ROWS, V42, V43,
                               V44, families, row)

DAY = date(2026, 10, 21)
ITEM = {item.item_id: item for item in ITEMS}


def equipment(eid, site, line, version, group=M_C4, family=F_C4, hpa=False):
    return EquipmentRecord(eid, f"DM-{eid:03d}", DAY, site, line, "M-C4",
                           (PmVersion(None, group, family, version),), is_hpa=hpa)


RECORDS = (
    equipment(1, "평택", "P1", "v4.56.6.42"),   # A 적용
    equipment(2, "평택", "P2", "v4.56.6.41"),   # A 미적용 (v41에는 없음)
    equipment(3, "평택", "P1", "v4.56.6.44"),   # A 적용
    equipment(4, "화성", "16L", "v4.56.6.41"),  # A 미적용
    equipment(5, "화성", "16L", None),          # 버전 없음 → 미확인
    equipment(6, "화성", "15L", "v2.1", group=M_O2, family=F_O2),  # O2: A 대상 외
    equipment(7, "평택", "P1", "v4.56.6.41", hpa=True),           # HPA 제외
    equipment(8, "기흥", "5L", "v4.56.6.43", group=M_C4_PLUS),     # A 적용, D도 적용
)


def overview(item_id, catalog=None, records=RECORDS):
    return (catalog or ItemCatalog(families(), ROWS)).overview(ITEM[item_id], records)


def test_first_included_and_all_included_versions_follow_release_order():
    a = overview(ITEM_A).families
    c = overview(ITEM_C).families

    assert [(f.family_id, f.first_included.release_version_id) for f in a] == [(F_C4, V42)]
    assert [r.release_version_id for r in a[0].included] == [V42, V43, V44]
    assert [r.release_version_id for r in c[0].included] == [V42, V43]  # v44에서 제거


def test_unordered_release_is_listed_after_ordered_and_never_first():
    extra = ReleaseVersion(399, F_C4, "v4.56.6.40x", None)
    catalog = ItemCatalog(families(extra), [*ROWS, row(399, ITEM_A)])

    included = overview(ITEM_A, catalog).families[0]

    assert included.first_included.release_version_id == V42
    assert [r.release_version_id for r in included.included] == [V42, V43, V44, 399]


def test_counts_split_into_four_states_and_keep_unknown_and_not_target_out_of_rate():
    """BE-014: 적용·미적용·미확인·대상 외로 나눠 전달한다. O2 설비(6번)는 대상 외로 따로 센다."""
    result = overview(ITEM_A)

    assert result.total == ApplicationCount(applied=3, not_applied=2, unknown=1, not_target=1)
    assert result.total.rate == 0.6 and result.hpa_excluded == 1
    assert dict(result.by_site) == {
        "기흥": ApplicationCount(1, 0, 0, 0), "평택": ApplicationCount(2, 1, 0, 0), "화성": ApplicationCount(0, 1, 1, 1)}


def test_missing_equipment_is_sorted_by_site_line_and_code():
    missing = overview(ITEM_A).missing

    assert [(e.record.equipment_code, e.record.site, e.record.line) for e in missing] == [
        ("DM-002", "평택", "P2"), ("DM-004", "화성", "16L")]


def test_listed_scope_counts_other_models_as_not_target():
    """D는 C4 Plus 전용이라 일반 C4 설비는 버전이 비어 있어도 대상 외로 센다. 미적용과 섞지 않는다."""
    result = overview(ITEM_D)

    assert result.total == ApplicationCount(applied=1, not_applied=0, unknown=0, not_target=6)
    assert result.total.rate == 1.0 and result.missing == ()
    assert dict(result.by_site)["평택"] == ApplicationCount(0, 0, 0, 3)


def test_item_without_rows_has_no_versions_and_no_rate():
    result = overview(ITEM_A, ItemCatalog(families(), []))

    assert result.families == () and result.missing == ()
    assert result.total == ApplicationCount(not_target=7) and result.total.rate is None
