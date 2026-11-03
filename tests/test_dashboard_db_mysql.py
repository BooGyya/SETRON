"""DB에 저장한 현황이 대시보드 공급자로 이어지는지 실제 MySQL 5.6.19에서 검증한다 (Jira S15P31B106-75)."""

import os
from datetime import date

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.import_save.service import DatabaseImportSaver
from setron.ui.database_source import (
    DatabaseDashboardSource,
    dashboard_source_from_env,
    load_dashboard_source,
)
from setron.ui.query_state import AppliedQuery, QuerySelection, TargetN
from test_import_save_mysql import EQUIPMENT, request, row, storage  # noqa: F401  시드 적용 fixture 재사용
from test_schema_mysql import schema_database  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")

ALL = AppliedQuery(QuerySelection(), 1, 0, "전체")


def equipment(row_id, code, model, family, version="v1", **changes):
    values = {**EQUIPMENT, "equipment_code": code, "model_name": model, "family_name": family,
              "version_raw": version, **changes}
    return row(row_id, {"equipment_code": f"C{row_id}", "version_raw": f"F{row_id}"}, **values)


def save(storage, request_id, *rows, kind=ExtractKind.EQUIPMENT_STATUS):  # noqa: F811
    DatabaseImportSaver(storage).save(request(kind, list(rows), request_id))


def test_empty_db_has_no_observation(storage):  # noqa: F811
    source = load_dashboard_source(storage)

    assert source.records == () and "현황 0건" in source.source_label
    with pytest.raises(ValueError, match="기준 관측일"):
        source.snapshot(ALL)


def test_saved_equipment_feeds_dashboard_and_unknown_site_is_skipped(storage):  # noqa: F811
    save(storage, "dash-1-hwaseong",
         equipment(1, "D-C4", "M-C4", "C4"),
         equipment(3, "D-HPA", "HPA", "HPA", group_name="HPA"))
    save(storage, "dash-1-pyeongtaek",
         equipment(2, "D-O2", "M-O2", "O2", site="평택", line="P1"))
    save(storage, "dash-1-unknown-site",
         equipment(4, "D-BUSAN", "M-O2", "O2", site="부산"))

    source = load_dashboard_source(storage)
    snapshot = source.snapshot(ALL)

    assert {r.equipment_code for r in source.records} == {"D-C4", "D-O2", "D-HPA"}
    assert "미연결 또는 확인 필요 1건 제외" in source.source_label
    assert snapshot.base_date == date(2026, 10, 21)
    assert (snapshot.summary.equipment_count, snapshot.summary.hpa_excluded) == (2, 1)
    by_code = {r.equipment_code: r for r in source.records}
    assert source.locations[by_code["D-O2"].equipment_id].site_key == "pyeongtaek"
    assert source.locations[by_code["D-O2"].equipment_id].raw_area == "P1"
    assert by_code["D-HPA"].is_hpa


def test_groups_bind_to_registered_models(storage):  # noqa: F811
    source = load_dashboard_source(storage)

    assert len(source.bindings["o5"]) == 2  # M-O5와 M-O5 Prime
    assert source.bindings["inline-ox"] == () and source.bindings["cluster-ox"] == ()
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
