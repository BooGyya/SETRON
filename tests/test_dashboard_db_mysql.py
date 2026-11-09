"""DB에 저장한 현황이 대시보드 공급자로 이어지는지 실제 MySQL 5.6.19에서 검증한다 (Jira S15P31B106-75)."""

import os
from datetime import date

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.equipment.repository import EquipmentRepository
from setron.features.risk.schemas import MappingStatus, RiskStatus, RuleType
from setron.ui.database_source import (
    DatabaseDashboardSource,
    dashboard_source_from_env,
    load_dashboard_source,
)
from setron.ui.dashboard_data import EquipmentLocation, RiskRuleState
from setron.ui.query_state import AppliedQuery, QuerySelection, TargetN, preview_catalog
from test_import_save_mysql import EQUIPMENT, request, row, storage  # noqa: F401  시드 적용 fixture 재사용
from test_schema_mysql import schema_database  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")

ALL = AppliedQuery(QuerySelection(), 1, 0, "전체")


def equipment(row_id, code, model, family, version="v1", **changes):
    values = {**EQUIPMENT, "equipment_code": code, "model_name": model, "family_name": family,
              "version_raw": version, **changes}
    return row(row_id, {"equipment_code": f"C{row_id}", "model_name": f"D{row_id}", "version_raw": f"F{row_id}"}, **values)


def save(storage, request_id, *rows, kind=ExtractKind.EQUIPMENT_STATUS):  # noqa: F811
    DatabaseImportSaver(storage).save(request(kind, list(rows), request_id))


def test_empty_db_has_no_observation(storage):  # noqa: F811
    source = load_dashboard_source(storage)

    assert source.records == () and "현황 0건" in source.source_label
    with pytest.raises(ValueError, match="기준 관측일"):
        source.snapshot(ALL)


def test_saved_equipment_feeds_dashboard_and_db_only_site_becomes_choice(storage):  # noqa: F811
    save(storage, "dash-1-hwaseong",
         equipment(1, "D-C4", "M-C4", "C4"),
         equipment(3, "D-HPA", "HPA", "HPA", group_name="HPA"))
    save(storage, "dash-1-pyeongtaek",
         equipment(2, "D-O2", "M-O2", "O2", site="평택", line="P1"))
    save(storage, "dash-1-db-only-site",
         equipment(4, "D-BUSAN", "M-O2", "O2", site="부산", line="B1"))

    source = load_dashboard_source(storage)
    snapshot = source.snapshot(ALL)

    assert {r.equipment_code for r in source.records} == {"D-C4", "D-O2", "D-HPA", "D-BUSAN"}
    assert "제외" not in source.source_label
    assert snapshot.base_date == date(2026, 10, 21)
    assert (snapshot.summary.equipment_count, snapshot.summary.hpa_excluded) == (3, 1)
    by_code = {r.equipment_code: r for r in source.records}
    assert source.locations[by_code["D-O2"].equipment_id] == EquipmentLocation("pyeongtaek", "P1", None, "pyeongtaek:P1")
    assert source.locations[by_code["D-BUSAN"].equipment_id].site_key == "부산"
    assert by_code["D-HPA"].is_hpa
    # 선택지: VER3 Site 키는 유지하고 DB에만 있는 Site·관측된 Line을 덧붙인다.
    assert [o.key for o in source.catalog.sites][:3] == [o.key for o in preview_catalog().sites]
    assert ("부산", "부산") in {(o.key, o.label) for o in source.catalog.sites}
    assert {("pyeongtaek:P1", "pyeongtaek"), ("부산:B1", "부산")} <= {(o.key, o.site_key) for o in source.catalog.lines}
    assert all(o.fab_key is None for o in source.catalog.lines)
    busan = AppliedQuery(QuerySelection(site_key="부산", line_key="부산:B1"), 2, 0, "부산 B1")
    assert source.snapshot(busan).summary.equipment_count == 1


def test_catalog_stays_fixed_until_reload(storage):  # noqa: F811
    # storage는 모듈 단위로 공유되므로 이 테스트만의 설비 코드·Site를 쓴다.
    live = DatabaseDashboardSource(lambda catalog=None: load_dashboard_source(storage, catalog))
    before = live.catalog
    assert "울산" not in {o.key for o in before.sites}

    save(storage, "catalog-new-site", equipment(1, "D-ULSAN", "M-O2", "O2", site="울산", line="U1"))
    live.refresh()

    assert live.catalog == before  # 조회 중 선택지 고정: 새 Site 현황은 아직 연결하지 않는다
    assert "D-ULSAN" not in {r.equipment_code for r in live.records}
    assert "제외" in live.source_label

    reloaded = live.reload_catalog()

    assert reloaded is live.catalog and "울산" in {o.key for o in reloaded.sites}
    assert "울산:U1" in {o.key for o in reloaded.lines}
    assert "D-ULSAN" in {r.equipment_code for r in live.records}


def test_groups_bind_to_registered_models(storage):  # noqa: F811
    source = load_dashboard_source(storage)

    assert len(source.bindings["o5"]) == 2  # M-O5와 M-O5 Prime
    assert len(source.bindings["c4"]) == 3  # M-C4·M-C4 DFB·M-C4+ (개조형은 원 설비군, 멘토 확정 10/21·10/29)
    assert len(source.bindings["o3"]) == 2  # M-O3·XMO3
    # 시드 v2 에는 KCTC 4종이 모두 등록돼 있다 (멘토 확정 10/29). 등록 모델이 없는 설비군만 선택지에서 뺀다.
    assert len(source.bindings["inline-ox"]) == 1 and len(source.bindings["cluster-ox"]) == 1
    assert {o.key for o in source.catalog.groups} == set(source.bindings)
    assert {"c3", "c4", "o2", "o3", "o5", "inline-ox", "inline-o2", "cluster-ox", "cluster-o2"} <= set(source.bindings)
    c4 = next(cell for cell in source.snapshot(ALL).groups if cell.group_keys == ("c4",))
    assert len(c4.equipment_ids) == 1


def test_live_source_reads_new_saves_and_keeps_chosen_targets(storage):  # noqa: F811
    live = DatabaseDashboardSource(lambda: load_dashboard_source(storage))
    before = live.snapshot(ALL).summary.equipment_count
    pairs = {(p.model_id, p.family_id) for p in [*live.align_targets(())]}
    chosen = tuple(TargetN(m, f, None) for m, f in pairs)

    save(storage, "release-c4", row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v2", content="변경",
                                    model_name="M-C4", family_name="C4"), kind=ExtractKind.RELEASE_NOTE)
    save(storage, "dash-2", equipment(5, "D-O3", "M-O3", "O3"))
    query = AppliedQuery(QuerySelection(), 2, 0, "전체", chosen)
    after = live.snapshot(query)
    assert after.query is query  # 화면이 결과를 받아들이려면 같은 조회 객체여야 한다

    assert after.summary.equipment_count == before + 1
    aligned = live.align_targets(chosen)
    assert len(aligned) == len(pairs) + 1 and all(t.release_id is None for t in aligned)
    assert after.target_values == ()  # 목표 미지정은 계산에 쓰지 않는다
    c4_family = next(f for f in live.families.values() if any(r.version_raw == "v2" for r in f.releases))
    assert [r.version_order for r in c4_family.releases] == [None]  # 순서는 추정하지 않는다


def test_source_from_env():
    assert dashboard_source_from_env({}) == (None, None)
    source, problem = dashboard_source_from_env({
        "SETRON_DB_HOST": "127.0.0.1", "SETRON_DB_PORT": "1", "SETRON_DB_NAME": "setron",
        "SETRON_DB_USER": "u", "SETRON_DB_PASSWORD": "p", "SETRON_DB_CONNECT_TIMEOUT": "1"})
    assert source is None and problem.startswith("DB 자료를 읽지 못해")


def test_risk_rules_in_db_decide_dashboard_risk(storage):  # noqa: F811
    """위험 지정이 없으면 미확인, 사용 중인 지정이 있으면 DB 기준으로 판정한다 (BE-015)."""
    before = load_dashboard_source(storage)
    assert "위험 기준 미등록" in before.source_label
    assert before.snapshot(ALL).risk_rule_state is RiskRuleState.UNREGISTERED
    assert set(before.risks.values()) == {RiskStatus.UNKNOWN}

    save(storage, "release-o3", row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v1", content="결함 버전",
                                    model_name="M-O3", family_name="O3"), kind=ExtractKind.RELEASE_NOTE)
    with storage.transaction() as tx:
        rows = EquipmentRepository().rows
        o3 = [f for f in rows(tx, "version_family") if f["family_name"] == "O3"][0]
        release = [r for r in rows(tx, "release_version", family_id=o3["family_id"])
                   if r["version_raw"] == "v1"][0]
        base = {"family_id": o3["family_id"], "risk_reason": "가상 결함", "target_reference": {"family": "O3"},
                "release_version_id": release["release_version_id"], "mapping_status": "VERIFIED"}
        tx.insert("risk_rule", {**base, "rule_type": "DIRECT_VERSION", "is_enabled": False, "risk_reason": "끈 지정"})

    disabled = load_dashboard_source(storage)
    assert disabled.snapshot(ALL).risk_rule_state is RiskRuleState.ALL_DISABLED
    with storage.transaction() as tx:
        tx.insert("risk_rule", {**base, "rule_type": "DIRECT_VERSION"})

    after = load_dashboard_source(storage)
    # 판정기는 사용 중인 지정만 쓴다 (끈 지정은 판정에서 뺀다).
    assert [(r.rule_type, r.is_enabled, r.mapping_status) for r in after.assessor.rules] == [
        (RuleType.DIRECT_VERSION, True, MappingStatus.VERIFIED)]
    by_code = {r.equipment_code: r.equipment_id for r in after.records}
    assert "위험 기준 1건" in after.source_label
    assert after.risks[by_code["D-O3"]] is RiskStatus.RISK
    assert after.risks[by_code["D-O2"]] is RiskStatus.NO_RISK
    snapshot = after.snapshot(ALL)
    assert snapshot.risk_rule_state is RiskRuleState.ACTIVE
    assert len(snapshot.risky_ids) == 1
    from setron.ui.heatmap_data import equipment_tiles
    tile = next(t for t in equipment_tiles(snapshot, after) if t.code == "D-O3")
    assert "가상 결함" in tile.risk_detail and "끈 지정" not in tile.risk_detail
