"""DB 없이 서비스 반환 계약과 B 읽기 변환을 검증한다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest

from setron.features.equipment.schemas import StoredPm, StoredSnapshot, StoredSoftware
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import LinkedItemView, RecordView, ReleaseDetail, SourceCell
from setron.features.storage.validation import StorageValidationError
from setron.features.version.service import sort_releases
from setron.features.aggregation.service import summarize
from setron.ui.read_converters import (
    read_equipment_record,
    read_equipment_records,
    read_item,
    read_item_links,
    read_pm_versions,
    read_release,
)


def record(table="release_version", **changes):
    """서비스 형태의 합성 조회 결과를 만든다.

    param
    - table: 릴리즈 또는 Item 테이블.
    - changes: 현재값에 덮어쓸 필드.

    return
    - 실제 DB를 사용하지 않는 RecordView.
    """
    row = dict(batch_id=7, release_version_id=1, family_id=2, version_raw=" V9_H ",
               version_order=None, released_at=None, item_mapping_status="UNKNOWN") if table == "release_version" else dict(
                   batch_id=8, item_id=3, item_no=None, content="현재 정정 내용")
    row.update(changes)
    return RecordView(table=table, current=row, source_type="EXCEL", imported_at=datetime(2026, 11, 2),
                      original_cells=tuple(SourceCell(field="version_raw" if table == "release_version" else "content",
                                                     sheet=sheet, address="A1", value=value)
                                           for sheet, value in (("원본1", None), ("원본2", False))))


def snapshot(**changes):
    """복수 SW·PM을 가진 합성 관측을 만든다.

    param
    - changes: 관측 필드 변경값.

    return
    - 실제 설비 ID와 관측 ID가 다른 StoredSnapshot.
    """
    pm = StoredPm(pm_id=12, batch_id=7, pm_name="PM1", group_id=4)
    sw = StoredSoftware(equipment_version_id=10, batch_id=7, family_id=2, version_raw="V9", pms=(pm,))
    value = StoredSnapshot(equipment_id=1, snapshot_id=99, equipment_code="EQ1", base_date=date(2026, 11, 1),
                           site="P1", model_name="혼합", batch_id=7, group_id=None,
                           software=(sw, replace(sw, equipment_version_id=11, family_id=3, version_raw="V1",
                                                 pms=(replace(pm, pm_id=13, pm_name="PM2"),))))
    return replace(value, **changes)


def test_release_preserves_unknown_raw_and_multiple_sources():
    """미확인·원문·여러 출처를 보존하고 입력을 바꾸지 않는지 검증한다."""
    view = record()
    before = deepcopy(view)
    result = read_release(view)
    assert result.release.version_raw == " V9_H "
    assert result.release.version_order is None and result.release.released_at is None
    assert not result.items_verified
    assert [(s.sheet, s.cells, s.batch_id) for s in result.sources] == [("원본1", "A1", 7), ("원본2", "A1", 7)]
    assert all(s.document is None and s.revision is None for s in result.sources)
    assert view == before and view.original_cells[1].value is False
    assert not hasattr(result, "model_ids")


def test_confirmed_order_not_version_text():
    """확인된 순서와 동일 순서·미확인 상태를 기존 정렬에 전달한다."""
    values = [read_release(record(release_version_id=i, version_raw=text, version_order=order,
                                  item_mapping_status="VERIFIED")).release
              for i, text, order in ((1, "V99", 1), (2, "V2", 2), (3, "V3", 2), (4, "V100", None))]
    result = sort_releases(values)
    assert [r.version_raw for r in result.ordered] == ["V99", "V2", "V3"]
    assert result.duplicated_orders == (2,)
    assert result.unordered == (values[3],)
    assert read_release(record(item_mapping_status="VERIFIED")).items_verified


@pytest.mark.parametrize("changes", [dict(family_id=True), dict(release_version_id="1"),
    dict(version_order=True), dict(version_order="2"), dict(released_at="2026-11-01"),
    dict(released_at=datetime(2026, 11, 1)), dict(item_mapping_status="INVALID"),
    dict(version_raw=b"V1"), dict(batch_id=None)])
def test_invalid_release_rejected(changes):
    """미확인과 잘못된 값을 구분한다.

    param
    - changes: 서비스 계약을 위반하는 값.
    """
    with pytest.raises(StorageValidationError):
        read_release(record(**changes))


def test_missing_field_and_wrong_table_rejected():
    """누락된 필드를 None으로 숨기거나 다른 테이블을 해석하지 않는다."""
    view = record()
    del view.current["version_order"]
    with pytest.raises(StorageValidationError):
        read_release(view)
    with pytest.raises(StorageValidationError):
        read_release(record("item"))


def test_item_uses_current_value_and_preserves_original():
    """Item 번호와 원본값을 현재 관리값으로 덮어쓰지 않는다."""
    view = record("item")
    result = read_item(view)
    assert result.item.item_id == 3 and result.item.item_no is None
    assert result.item.content == "현재 정정 내용"
    assert view.original_cells[0].value is None
    assert read_item(replace(view, original_cells=())).sources == ()


@pytest.mark.parametrize("state", list(ItemState))
def test_explicit_links_only(state):
    """저장된 모든 상태와 모델 범위를 보존하며 빈 목록을 상속하지 않는다.

    param
    - state: 저장 상태 3종.
    """
    link = LinkedItemView(item=record("item"), item_state=state, target_scope=TargetScope.LISTED, target_group_ids=(4, 5))
    detail = ReleaseDetail(release=record(), items=(link,))
    result = read_item_links(detail)
    assert result[0].state is state and result[0].target_group_ids == (4, 5)
    assert result[0].release_version_id == 1 and result[0].item_id == 3
    assert read_item_links(replace(detail, items=())) == ()
    with pytest.raises(StorageValidationError):
        read_item_links(replace(detail, items=(link, link)))


@pytest.mark.parametrize("scope,ids", [(TargetScope.LISTED, ()), (TargetScope.ALL, (4,)),
    (TargetScope.UNKNOWN, (4,)), (TargetScope.LISTED, (4, 4)), (TargetScope.LISTED, (True,)), ("ALL", ())])
def test_invalid_scope_rejected(scope, ids):
    """비어 있거나 모순된 모델 범위를 거부한다.

    param
    - scope: 검사할 범위.
    - ids: 검사할 모델 ID.
    """
    link = LinkedItemView(item=record("item"), item_state=ItemState.UNKNOWN, target_scope=scope, target_group_ids=ids)
    with pytest.raises(StorageValidationError):
        read_item_links(ReleaseDetail(release=record(), items=(link,)))


def test_multiple_software_and_explicit_representative_model():
    """각 PM의 다른 SW 계열을 보존하고 대표 모델은 확인된 경우만 사용한다."""
    value = snapshot()
    before = deepcopy(value)
    result = read_pm_versions(value)
    assert [(p.pm_name, p.group_id, p.family_id, p.version_raw) for p in result] == [
        ("PM1", 4, 2, "V9"), ("PM2", 4, 3, "V1")]
    assert all(p.release_version_id is None for p in result)
    assert value == before
    no_pm = replace(value, software=(replace(value.software[0], pms=()),))
    with pytest.raises(StorageValidationError):
        read_pm_versions(no_pm)
    result = read_pm_versions(replace(no_pm, group_id=6))
    assert result[0].pm_name is None and result[0].group_id == 6


def test_duplicate_pm_and_missing_date_rejected():
    """같은 계열 PM 중복과 기준일 누락을 임의 보정하지 않는다."""
    value = snapshot()
    with pytest.raises(StorageValidationError):
        read_pm_versions(replace(value, software=(value.software[0], value.software[0])))
    with pytest.raises(StorageValidationError):
        read_pm_versions(replace(value, base_date=None))


def test_cross_family_pm_conflict_and_partial_mapping_rejected():
    """저장 서비스가 금지하는 PM 위치 충돌과 일부 SW의 PM 미연결을 거부한다."""
    value = snapshot()
    first, second = value.software
    with pytest.raises(StorageValidationError):
        read_pm_versions(replace(value, software=(first, replace(second, pms=first.pms))))
    with pytest.raises(StorageValidationError):
        read_pm_versions(replace(value, group_id=4, software=(first, replace(second, pms=()))))


def hpa_snapshot(**changes):
    """PM 없는 SW 하나와 대표 모델을 가진 단일 모델 관측을 만든다."""
    sw = StoredSoftware(equipment_version_id=20, batch_id=7, family_id=2, version_raw="V9")
    value = StoredSnapshot(equipment_id=5, snapshot_id=50, equipment_code="HPA1", base_date=date(2026, 11, 1),
                           site="P1", model_name="HPA", batch_id=7, group_id=9, software=(sw,))
    return replace(value, **changes)


def test_equipment_record_keeps_snapshot_fields_and_pms():
    """관측 필드와 PM·SW 연결을 그대로 옮기고 원본 모델명을 바꾸지 않는다."""
    value = snapshot(line="L1")
    before = deepcopy(value)
    record = read_equipment_record(value, {9})
    assert (record.equipment_id, record.equipment_code, record.base_date, record.site, record.line,
            record.model_name, record.is_hpa) == (1, "EQ1", date(2026, 11, 1), "P1", "L1", "혼합", False)
    assert record.pms == read_pm_versions(value)
    assert value == before
    assert read_equipment_record(snapshot(), ()).line is None


def test_hpa_is_decided_by_excluded_models_only():
    """모델명이 아니라 analysis_enabled = false 모델 ID로만 HPA를 판단한다."""
    assert read_equipment_record(hpa_snapshot(), {9}).is_hpa
    assert not read_equipment_record(hpa_snapshot(), ()).is_hpa
    assert read_equipment_record(snapshot(), {4}).is_hpa


def test_mixed_excluded_and_analysis_models_rejected():
    """대표 모델과 PM 모델 중 일부만 제외 모델이면 임의로 판단하지 않는다."""
    with pytest.raises(StorageValidationError):
        read_equipment_record(snapshot(group_id=6), {4})
    with pytest.raises(StorageValidationError):
        read_equipment_record(snapshot(group_id=6), {6})


@pytest.mark.parametrize("changes", [
    {"equipment_id": 0}, {"equipment_code": " "}, {"site": ""}, {"line": " "},
    {"model_name": None}, {"equipment_code": "E" * 51},
])
def test_invalid_equipment_fields_rejected(changes):
    with pytest.raises(StorageValidationError):
        read_equipment_record(snapshot(**changes), ())


@pytest.mark.parametrize("excluded", ["9", (0,), (True,)])
def test_invalid_excluded_ids_rejected(excluded):
    with pytest.raises(StorageValidationError):
        read_equipment_record(hpa_snapshot(), excluded)


def test_records_reject_duplicate_observation_and_keep_dates():
    """같은 설비·같은 기준일 관측은 거부하고, 다른 기준일은 함께 변환한다."""
    later = snapshot(snapshot_id=100, base_date=date(2026, 11, 2))
    records = read_equipment_records([snapshot(), later], ())
    assert [r.base_date for r in records] == [date(2026, 11, 1), date(2026, 11, 2)]
    with pytest.raises(StorageValidationError):
        read_equipment_records([snapshot(), snapshot(snapshot_id=101)], ())


def test_records_feed_common_summary():
    """변환 결과를 공통 집계에 바로 넣을 수 있고 HPA는 설비 수에서 빠진다."""
    records = read_equipment_records([snapshot(), hpa_snapshot()], {9})
    summary = summarize(records, {}, {})
    assert (summary.equipment_count, summary.hpa_excluded) == (1, 1)
