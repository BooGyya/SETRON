"""관리 수정 후 DB 읽기 공급자의 Item·위험·원본 자료 최신화를 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import replace
from datetime import date
from threading import Event

import pytest

from setron.features.item.schemas import Application, ItemState, TargetScope
from setron.features.management import ManagementService
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.risk.schemas import RiskStatus
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseConnectionError
from setron.ui.database_source import DatabaseDashboardSource, load_dashboard_source
from setron.ui.heatmap_data import equipment_tiles
from setron.ui.item_data import item_data_from_source
from setron.ui.query_state import AppliedQuery, QuerySelection

from test_management_service import edit, observe
from test_release_revision import all_rows, observation, release, revision_storage


ALL = AppliedQuery(QuerySelection(), 1, 0, "전체")


def connected(storage):
    """UI 모델 이름과 실제 원본 좌표가 있는 릴리즈·Item·관측을 준비한다."""
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {
            "노트": {"A2": "v1", "B2": "원본 안전 개선"},
        }})
    service = ReleaseService(storage)
    detail = service.register_release(ReleaseInput(
        family_id=1, version_raw="v1", version_order=1, item_mapping_status="VERIFIED",
        batch_id=batch["batch_id"], source_reference={"version_raw": {"sheet": "노트", "cells": ["A2"]}},
    ), (ItemInput(content="원본 안전 개선", item_no=7, batch_id=batch["batch_id"],
                  source_reference={"content": {"sheet": "노트", "cells": ["B2"]}}),))
    release_id = detail.release.current["release_version_id"]
    item_id = detail.items[0].item.current["item_id"]
    service.save_item_links(release_id, (ItemLinkInput(item_id=item_id, item_state=ItemState.NOT_APPLIED,
                                                     target_scope=TargetScope.ALL),))
    snapshot = observe(storage, release_id)
    with storage.transaction() as tx:
        risk = tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1,
            "item_id": item_id, "risk_reason": "검증용 필수 안전 개선", "target_reference": {},
            "mapping_status": "VERIFIED"})
    return release_id, item_id, snapshot, risk["risk_rule_id"]


def test_database_catalog_uses_stored_items_links_and_original_positions_without_writes(revision_storage):
    """DB 읽기는 관리값·원본을 바꾸지 않고 실제 Item과 원본 좌표를 화면 자료로 전달한다."""
    storage = revision_storage
    release_id, item_id, _, _ = connected(storage)
    before = {table: deepcopy(all_rows(storage, table)) for table in TABLE_NAMES}
    source = load_dashboard_source(storage)
    catalog = source.version_catalog
    assert catalog.pairs[0].model_name == "M-O2"
    assert catalog.pairs[0].family_name == "O2"
    assert catalog.entry(release_id).items_verified
    assert catalog.items[0].item.item_id == item_id
    assert catalog.items[0].item.content == "원본 안전 개선"
    assert catalog.items[0].sources[0].sheet == "노트"
    assert catalog.items[0].sources[0].cells == "B2"
    assert catalog.entry(release_id).sources[0].cells == "A2"
    assert catalog.items[0].sources[0].document is None
    assert catalog.version_items[0].state is ItemState.NOT_APPLIED
    data = item_data_from_source(catalog, source)
    assert data.catalog().equipment_application(item_id, data.records[0]).application is Application.NOT_APPLIED
    assert source.snapshot(ALL).risky_ids == (data.records[0].equipment_id,)
    assert {table: all_rows(storage, table) for table in TABLE_NAMES} == before


def test_management_save_refreshes_item_text_application_and_risk_on_next_load(revision_storage):
    """같은 공급자를 재조회하면 수동 수정한 Item 내용·적용 상태·위험을 함께 갱신한다."""
    storage = revision_storage
    release_id, item_id, _, _ = connected(storage)
    source = DatabaseDashboardSource(lambda: load_dashboard_source(storage))
    assert source.snapshot(ALL).risky_ids
    service = ManagementService(storage)
    service.save((
        edit(service, "item", {"item_id": item_id}, {"content": "수정한 안전 개선 내용"}),
        edit(service, "release_version_item", {"release_version_id": release_id, "item_id": item_id},
             {"item_state": "APPLIED"}),
    ))
    catalog = source.load_version_catalog()
    assert catalog.items[0].item.content == "수정한 안전 개선 내용"
    assert catalog.version_items[0].state is ItemState.APPLIED
    assert catalog.items[0].sources[0].cells == "B2"
    data = item_data_from_source(catalog, source)
    assert data.catalog().equipment_application(item_id, data.records[0]).application is Application.APPLIED
    assert source.snapshot(ALL).risky_ids == ()
    assert source.risk_status(source.records[0]) is RiskStatus.NO_RISK


def test_out_of_order_observations_keep_current_location_after_each_date_edit(revision_storage):
    """늦게 저장한 과거 날짜의 위치가 현재 대시보드·타일 위치를 덮지 않는다."""
    storage = revision_storage
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
    release_id = release(storage)
    current = observe(storage, release_id, day=date(2026, 10, 2))
    past = observe(storage, release_id, day=date(2026, 10, 1))
    assert current.snapshot_id < past.snapshot_id
    source = DatabaseDashboardSource(lambda: load_dashboard_source(storage))
    service = ManagementService(storage)
    service.save((edit(service, "equipment_snapshot", {"snapshot_id": current.snapshot_id},
                       {"line": "현재 수정 라인"}),))

    def assert_current_location():
        snapshot = source.snapshot(ALL)
        assert snapshot.base_date == date(2026, 10, 2)
        assert snapshot.selected_records[0].line == "현재 수정 라인"
        assert snapshot.rows[0].position == ("hwaseong", "현재 수정 라인")
        assert "원본 구역 현재 수정 라인" in equipment_tiles(snapshot, source)[0].location
        assert source.locations[current.equipment_id].raw_area == "현재 수정 라인"

    assert_current_location()
    service.save((edit(service, "equipment_snapshot", {"snapshot_id": past.snapshot_id},
                       {"line": "과거 수정 라인"}),))
    assert_current_location()
    assert {record.base_date: record.line for record in source.records} == {
        date(2026, 10, 1): "과거 수정 라인", date(2026, 10, 2): "현재 수정 라인",
    }


def test_out_of_order_hpa_locations_keep_independent_latest_observation(revision_storage):
    """HPA 최신 관측일·위치는 일반 분석 기준일 및 저장 순서와 독립적이다."""
    storage = revision_storage
    connected(storage)
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 4, "family_name": "HPA", "ui_type": None})
    release_id = release(storage, family=3, raw="hpa-v1")
    latest, _ = observation(storage, day=date(2026, 10, 5), code="HPA-1",
                            versions=((3, 4, "hpa-v1", release_id),))
    past, _ = observation(storage, day=date(2026, 10, 3), code="HPA-1",
                          versions=((3, 4, "hpa-v1", release_id),))
    service = ManagementService(storage)
    service.save((edit(service, "equipment_snapshot", {"snapshot_id": latest}, {"line": "HPA 현재 라인"}),))
    service.save((edit(service, "equipment_snapshot", {"snapshot_id": past}, {"line": "HPA 과거 라인"}),))
    source = DatabaseDashboardSource(lambda: load_dashboard_source(storage))
    snapshot = source.snapshot(ALL)
    assert snapshot.base_date == date(2026, 10, 1)
    assert snapshot.hpa_lookup_records[0].base_date == date(2026, 10, 5)
    assert dict(snapshot.hpa_lookup_locations)[snapshot.hpa_lookup_records[0].equipment_id].raw_area == "HPA 현재 라인"
    tile = next(tile for tile in equipment_tiles(snapshot, source) if tile.code == "HPA-1")
    assert tile.observation_date == date(2026, 10, 5)
    assert "원본 구역 HPA 현재 라인" in tile.location


def test_risk_lookup_preserves_each_actual_observation_date(revision_storage):
    """동일 설비의 과거 위험을 최신 날짜의 안전 판정으로 덮지 않는다."""
    storage = revision_storage
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
    first = release(storage, raw="v1", order=1)
    second = release(storage, raw="v2", order=2)
    observation(storage, day=date(2026, 10, 1), versions=((1, 1, "v1", first),))
    observation(storage, day=date(2026, 10, 2), versions=((1, 1, "v2", second),))
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": first, "risk_reason": "v1 직접 위험", "target_reference": {},
            "mapping_status": "VERIFIED"})
    source = load_dashboard_source(storage)
    records = sorted(source.records, key=lambda record: record.base_date)
    assert records[0].equipment_id == records[1].equipment_id
    assert source.risks_by_date[records[0].base_date, records[0].equipment_id] is RiskStatus.RISK
    assert source.risks_by_date[records[1].base_date, records[1].equipment_id] is RiskStatus.NO_RISK
    assert source.risk_status(records[0]) is RiskStatus.RISK
    assert source.risk_status(records[1]) is RiskStatus.NO_RISK
    assert source.snapshot(ALL).risky_ids == ()


def test_explicit_item_target_model_has_real_name_without_guessing_other_models(revision_storage):
    """명시된 파생 모델 범위는 표시하고 같은 설비군의 미지정 모델을 임의로 추가하지 않는다."""
    storage = revision_storage
    release_id, item_id, _, _ = connected(storage)
    ReleaseService(storage).save_item_links(release_id, (ItemLinkInput(
        item_id=item_id, item_state=ItemState.APPLIED, target_scope=TargetScope.LISTED, target_group_ids=(2,),
    ),))
    source = load_dashboard_source(storage)
    catalog = source.version_catalog
    assert next(pair for pair in catalog.pairs if pair.model_id == 2).model_name == "O2 Plus"
    assert {(pair.model_id, pair.family_id) for pair in catalog.pairs} == {(1, 1), (2, 1), (3, 2)}
    assert catalog.version_items[0].target_group_ids == (2,)
    assert "O2 Plus" in catalog.item_rows(release_id, 2)[0][2]


def test_unlinked_item_still_appears_and_missing_observations_are_not_invented(revision_storage):
    """관측이나 릴리즈 연결이 없는 저장 Item도 목록에서 빠뜨리지 않는다."""
    storage = revision_storage
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})
        item = tx.insert("item", {"content": "연결 검토 중 Item", "batch_id": batch["batch_id"]})
    source = load_dashboard_source(storage)
    data = item_data_from_source(source.version_catalog, source)
    assert [entry.item.item_id for entry in data.search()] == [item["item_id"]]
    assert not data.observations_connected and data.base_date is None
    assert data.records == ()


def test_failed_refresh_keeps_previous_catalog_and_observation(revision_storage):
    """DB 재조회 실패는 기존 카탈로그를 부분 갱신하지 않고 실패로 전달한다."""
    storage = revision_storage
    connected(storage)
    source = DatabaseDashboardSource(lambda: load_dashboard_source(storage))
    old_catalog, old_records = source.version_catalog, source.records

    def fail():
        raise DatabaseConnectionError("검증용 접속 실패")

    source._loader = fail
    with pytest.raises(RuntimeError, match="DB 자료"):
        source.load_version_catalog()
    assert source.version_catalog is old_catalog
    assert source.records is old_records


def test_hpa_observation_remains_available_but_is_excluded_from_common_risk_counts(revision_storage):
    """HPA 원본을 조회 자료에 유지하면서 일반 대수·위험 대수에는 포함하지 않는다."""
    storage = revision_storage
    connected(storage)
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 4, "family_name": "HPA", "ui_type": None})
    hpa_release = release(storage, family=3, raw="hpa-v1")
    observation(storage, code="HPA-1", versions=((3, 4, "hpa-v1", hpa_release),))
    source = load_dashboard_source(storage)
    hpa = next(record for record in source.records if record.is_hpa)
    snapshot = source.snapshot(ALL)
    assert snapshot.summary.equipment_count == 1
    assert snapshot.summary.hpa_excluded == 1
    assert hpa.equipment_id not in snapshot.risky_ids
    assert source.risks_by_date[hpa.base_date, hpa.equipment_id] is RiskStatus.UNKNOWN


def test_concurrent_item_read_and_refresh_keep_catalog_and_observations_together(revision_storage, monkeypatch):
    """다른 읽기가 진행돼도 Item은 자기 DB 읽기의 카탈로그·관측으로 함께 조립한다."""
    import setron.ui.item_data as item_module

    connected(revision_storage)
    base = load_dashboard_source(revision_storage)
    calls, entering_item, release_item, third_load = [], Event(), Event(), Event()

    def loader():
        number = len(calls) + 1
        calls.append(number)
        fresh = copy(base)
        fresh.records = tuple(replace(record, model_name=str(number)) for record in base.records)
        fresh.version_catalog = replace(base.version_catalog, items=tuple(
            replace(entry, item=replace(entry.item, content=str(number))) for entry in base.version_catalog.items))
        if number == 3:
            third_load.set()
        return fresh

    source = DatabaseDashboardSource(loader)
    original = item_module.item_data_from_source

    def delayed(catalog, current):
        entering_item.set()
        assert release_item.wait(2), "Item 자료 조립을 끝내도록 테스트를 해제해야 합니다."
        return original(catalog, current)

    monkeypatch.setattr(item_module, "item_data_from_source", delayed)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(source.load_item_data)
        try:
            assert entering_item.wait(2)
            refreshing = executor.submit(source.refresh)
            assert third_load.wait(2)
            refreshing.result(timeout=2)
        finally:
            release_item.set()
        data = pending.result(timeout=2)
        refreshing.result(timeout=2)
    assert data.versions.items[0].item.content == data.records[0].model_name == "2"
    assert source.records[0].model_name == "3"
