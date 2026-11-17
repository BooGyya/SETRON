"""SQL 값 비교의 선택·원본 연결·PM 구성·저장 경계를 검증한다."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from setron.features.db_import.merge import SqlImportError, Write
from setron.features.peer_sync import read_state, reconcile
from setron.features.db_import.service import _read_target
from setron.infrastructure.database import Database
from test_sql_import_service import backup, bundle, current, seed, target  # noqa: F401
from test_db_import_group_settings import _rows as group_rows
from test_sql_import_dump import _export


@pytest.fixture(autouse=True)
def peer_identity(monkeypatch):
    monkeypatch.setenv("SETRON_PEER_ID", "00000000-0000-0000-0000-000000000501")


def with_process(rows, process):
    rows["equipment_snapshot"] = [(*row, process) for row in rows["equipment_snapshot"]]
    return rows


def decisions(plan, **fields):
    return {difference.id: fields.get(difference.field, "CURRENT") for difference in plan.differences}


def test_review_requires_explicit_selections_before_any_write(target, tmp_path):
    seed(target, with_process(bundle(line="현재 라인"), "현재 공정"))
    service = target[2]
    plan = service.preview(backup(tmp_path, with_process(bundle(line="파일 라인", day=2), "파일 공정")), "review")
    assert {difference.field for difference in plan.differences} == {"line", "process_name"}
    assert plan.unresolved == 2
    before = current(target)
    with pytest.raises(SqlImportError, match="선택하지 않은"):
        service.apply(plan)
    assert current(target) == before


def test_review_combines_independent_fields_without_overwriting_retained_values(target, tmp_path):
    seed(target, with_process(bundle(line="현재 라인"), "현재 공정"))
    service = target[2]
    plan = service.preview(backup(tmp_path, with_process(bundle(line="파일 라인", day=2), "파일 공정")), "review")
    plan = service.resolve(plan, decisions(plan, process_name="INCOMING"))
    assert plan.unresolved == 0
    service.apply(plan)
    snapshot, = current(target)["equipment_snapshot"]
    assert (snapshot["line"], snapshot["process_name"]) == ("현재 라인", "파일 공정")


def test_retaining_a_value_from_legacy_file_records_the_explicit_decision(target, tmp_path):
    seed(target, bundle(line="현재 라인"))
    service = target[2]
    plan = service.preview(backup(tmp_path, bundle(line="다른 라인", day=2)), "review")
    service.apply(service.resolve(plan, decisions(plan)))
    state = read_state(current(target))
    selected = [event for event in state.events.values() if event["operation"] == "resolve" and event.get("selection")]
    assert selected
    assert any(receipt.get("fields", {}).get("line") == "CURRENT" for event in selected
               for receipt in event["selection"].get("entities", {}).values())
    assert current(target)["equipment_snapshot"][0]["line"] == "현재 라인"


@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_review_pm_software_configuration_is_one_atomic_choice(target, tmp_path, choice):
    existing = bundle()
    existing["equipment_pm"].append((2, 1, 1, b"PM2", 1, "O2", 1, None))
    seed(target, existing)
    service = target[2]
    plan = service.preview(backup(tmp_path, bundle(line="파일 라인", version=b"v2", day=2)), "review")
    configuration, = [difference for difference in plan.differences if difference.field == "PM·SW 구성"]
    assert configuration.grouped
    assert "계열 ID" not in str(configuration.incoming) and "모델 ID" not in str(configuration.incoming)
    plan = service.resolve(plan, decisions(plan, **{"PM·SW 구성": choice, "line": "INCOMING"}))
    service.apply(plan)
    after = current(target)
    assert after["equipment_snapshot"][0]["line"] == "파일 라인"
    assert [row["version_raw"] for row in after["equipment_version"]] == [b"v2" if choice == "INCOMING" else b"v1"]
    assert {row["pm_name"] for row in after["equipment_pm"]} == ({b"PM1"} if choice == "INCOMING" else {b"PM1", b"PM2"})
    assert all(row["equipment_version_id"] == after["equipment_version"][0]["equipment_version_id"]
               for row in after["equipment_pm"])


def test_mixed_field_selection_preserves_each_fields_actual_source_batch(target, tmp_path):
    existing = with_process(bundle(line="현재 라인"), "현재 공정")
    incoming = with_process(bundle(line="파일 라인", day=2), "파일 공정")
    for rows, line, process, day in ((existing, "현재 라인", "현재 공정", 1), (incoming, "파일 라인", "파일 공정", 2)):
        rows["import_batch"] = [(1, "EXCEL", datetime(2026, 11, day), json.dumps({"S": {"A2": line, "B2": process}}))]
        values = list(rows["equipment_snapshot"][0])
        values[12] = json.dumps({"line": {"sheet": "S", "cells": ["A2"]},
                                 "process_name": {"sheet": "S", "cells": ["B2"]}})
        rows["equipment_snapshot"] = [tuple(values)]
    seed(target, existing)
    service = target[2]
    plan = service.preview(backup(tmp_path, incoming), "review")
    service.apply(service.resolve(plan, decisions(plan, process_name="INCOMING")))
    stored = current(target)
    snapshot, = stored["equipment_snapshot"]
    sources = json.loads(snapshot["source_reference"])
    assert sources["line"]["batch_id"] == 1
    assert sources["process_name"]["batch_id"] != 1
    batches = {batch["batch_id"]: json.loads(batch["raw_data"]) for batch in stored["import_batch"]}
    assert batches[sources["line"]["batch_id"]]["S"]["A2"] == "현재 라인"
    assert batches[sources["process_name"]["batch_id"]]["S"]["B2"] == "파일 공정"


def test_review_rejects_unknown_or_malformed_choices(target, tmp_path):
    seed(target, bundle())
    service = target[2]
    plan = service.preview(backup(tmp_path, bundle(line="다른 라인", day=2)), "review")
    with pytest.raises(SqlImportError, match="검토 목록에 없는"):
        service.resolve(plan, {"unknown": "INCOMING"})
    with pytest.raises(SqlImportError, match="적용 값을 선택"):
        service.resolve(plan, {plan.differences[0].id: "anything"})


def test_apply_rebuilds_reviewed_plan_and_rejects_forged_writes(target, tmp_path):
    seed(target, bundle())
    service = target[2]
    plan = service.preview(backup(tmp_path, bundle(line="새 라인", day=2)), "review")
    plan = service.resolve(plan, decisions(plan, line="INCOMING"))
    before = deepcopy(current(target))
    forged = replace(plan, writes=tuple(replace(write, row={**write.row, "line": "다른 값"})
                                      if write.table == "equipment_snapshot" else write for write in plan.writes))
    with pytest.raises(SqlImportError, match="적용 계획이 바뀌었습니다"):
        service.apply(forged)
    assert current(target) == before


def test_resolve_rejects_target_changed_since_preview(target, tmp_path):
    seed(target, bundle())
    service = target[2]
    plan = service.preview(backup(tmp_path, bundle(line="새 라인", day=2)), "review")
    with target[0].begin() as connection:
        connection.execute(target[1]["equipment_snapshot"].update().values(line="동시 변경"))
    with pytest.raises(SqlImportError, match="미리보기 이후"):
        service.resolve(plan, decisions(plan, line="INCOMING"))


def test_legacy_missing_rows_never_become_deletion_requests(target, tmp_path):
    seed(target, bundle())
    incoming = bundle()
    for name in ("equipment_snapshot", "equipment_version", "equipment_pm"):
        incoming[name] = []
    plan = target[2].preview(backup(tmp_path, incoming), "review")
    assert not any(difference.field == "삭제" for difference in plan.differences)
    assert not any(write.action == "delete" for write in plan.writes)


@pytest.mark.parametrize("choice", ["CURRENT", "INCOMING"])
def test_n_group_current_and_history_are_selected_as_one_bundle(target, tmp_path, choice):
    """N 기준을 바꾸면서 이전 이력이 섞이거나 일부만 제거되지 않는다."""
    existing, incoming = bundle(), bundle(day=2)
    for rows, count in ((existing, 3), (incoming, 2)):
        rows["app_setting"] = [(row["setting_key"], row["setting_value"]) for row in group_rows(count, model=1)]
        rows["version_family"][0] = (2, 1, "O2", None)
        rows["equipment_version"][0] = (1, 1, 2, b"v1", None, 1, None)
        rows["release_version"] = [(i, None, 2, f"v{i}".encode(), None, None, i, "UNKNOWN", 1, None, "[]")
                                   for i in range(1, 4)]
    seed(target, existing)
    service = target[2]
    plan = service.preview(backup(tmp_path, incoming), "review")
    difference, = plan.differences
    assert difference.field == "N 그룹·저장 이력" and difference.grouped
    plan = service.resolve(plan, {difference.id: choice})
    assert plan.unresolved == 0 and len(plan.differences) == 1
    service.apply(plan)
    settings = [row for row in current(target)["app_setting"] if row["setting_key"].startswith(b"version_groups.")]
    state = json.loads(next(row["setting_value"] for row in settings if row["setting_key"].startswith(b"version_groups.v1:")))
    assert state["sequence"] == (3 if choice == "CURRENT" else 2)
    assert len(settings) == (4 if choice == "CURRENT" else 3)


@pytest.mark.parametrize("with_original_jira", [False, True])
def test_metadata_bearing_legacy_item_schema_keeps_raw_source_through_review_and_apply(target, tmp_path, with_original_jira):
    """교환 이력을 가진 구버전 SQL도 새 열 보강 후 같은 원본으로 재검토·저장한다."""
    source_dir = tmp_path / "old-source"
    source_dir.mkdir()
    setup = globals()["target"].__wrapped__(source_dir, SimpleNamespace(param="old"))
    source = next(setup)
    try:
        rows = bundle()
        rows["item"] = [(1, None, 7, "원문", 1, None, "[]")]
        if with_original_jira:
            raw = {"노트": {"A1": "Update내용", "B1": "JIRA", "A2": "원문", "B2": "ETOJ-987"}}
            rows["import_batch"] = [(1, "EXCEL", datetime(2026, 11, 1), json.dumps(raw, ensure_ascii=False))]
            rows["item"] = [(1, None, 7, "원문", 1,
                             json.dumps({"content": {"sheet": "노트", "cells": ["A2"]}}), "[]")]
        seed(source, rows)
        with Database(source[0]).transaction() as session:
            repository, saved, _ = _read_target(session)
            initial = reconcile(session, repository, rows=saved,
                                peer_id="00000000-0000-0000-0000-000000000502")
        saved = current(source)
        values = {name: [tuple(row[column] for column in source[1][name].columns.keys()) for row in records]
                  for name, records in saved.items()}
        path = tmp_path / "legacy-peer.sql"
        path.write_text(_export(values, legacy_item=True), encoding="utf-8")
        service = target[2]
        plan = service.preview(path, "review")
        assert "jira_code" not in plan._source["item"][0]
        assert plan.unresolved == 0
        reviewed = service.resolve(plan, {})
        assert "jira_code" not in reviewed._source["item"][0]
        service.apply(reviewed)
        stored = current(target)
        assert stored["item"][0]["jira_code"] == ("ETOJ-987" if with_original_jira else None)
        assert set(initial.events) <= set(read_state(stored).events)
    finally:
        setup.close()
