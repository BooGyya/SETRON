"""SQL 계층 설정의 항목별 병합·코드 충돌·최종 부모 연결을 검증한다."""

import json
import os

import pytest
from sqlalchemy import MetaData

from setron.features.db_import.merge import SqlImportError
from setron.features.db_import.service import SqlImportService
from setron.infrastructure.database import Database

from test_sql_import_service import backup, bundle, current, seed, target  # noqa: F401
from test_schema_mysql import schema_database  # noqa: F401


def registry(**values):
    return [(key.encode("utf-8"), json.dumps(value, ensure_ascii=False)) for key, value in values.items()]


def custom_bundle(*, source=False):
    suffix = "002" if source else "001"
    category, area = f"C000000{suffix}", f"A000000{suffix}"
    prefix = "파일" if source else "대상"
    rows = bundle(code=b"EQ-2" if source else b"EQ-1", group="O3" if source else "O2",
                  line=f"{prefix} 세부", day=2 if source else 1)
    group = list(rows["equipment_group"][0])
    group[2] = category
    rows["equipment_group"] = [tuple(group)]
    snapshot = list(rows["equipment_snapshot"][0])
    snapshot[7] = area
    rows["equipment_snapshot"] = [tuple(snapshot)]
    rows["app_setting"] = registry(**{
        "hierarchy.categories.v1": {"labels": {category: f"{prefix} 공정"}},
        "hierarchy.areas.v1": {"labels": {area: f"{prefix} 구역"}},
        "hierarchy.model_aliases.v1": {"aliases": {f"{prefix} 이전 모델": 1}},
        "registered_sites.v1": {"sites": ["화성", f"{prefix} 빈 사업장"]},
        "registered_lines.v1": {"lines": [
            {"site": "화성", "line": f"{prefix} Line", "fab": area, "parent_line": None},
            {"site": "화성", "line": f"{prefix} 세부", "fab": area, "parent_line": f"{prefix} Line"},
        ]},
    })
    return rows


def settings(target):
    return {row["setting_key"].decode(): json.loads(row["setting_value"])
            for row in current(target)["app_setting"]}


@pytest.mark.parametrize("mode", ["add", "overwrite"])
def test_sql_keeps_both_databases_custom_definitions_and_remaps_aliases(target, tmp_path, mode):
    seed(target, custom_bundle())
    service = target[2]
    path = backup(tmp_path, custom_bundle(source=True))
    service.apply(service.preview(path, mode))
    saved = settings(target)
    assert {"C000000001", "C000000002"} <= saved["hierarchy.categories.v1"]["labels"].keys()
    assert {"A000000001", "A000000002"} <= saved["hierarchy.areas.v1"]["labels"].keys()
    assert {"화성", "대상 빈 사업장", "파일 빈 사업장"} == set(saved["registered_sites.v1"]["sites"])
    assert {entry["line"] for entry in saved["registered_lines.v1"]["lines"]} == {
        "대상 Line", "대상 세부", "파일 Line", "파일 세부"}
    groups = {row["group_name"]: row["group_id"] for row in current(target)["equipment_group"]}
    assert saved["hierarchy.model_aliases.v1"]["aliases"] == {
        "대상 이전 모델": groups["O2"], "파일 이전 모델": groups["O3"]}
    assert service.preview(path, mode).changes == 0


@pytest.mark.parametrize("mode", ["add", "overwrite"])
def test_custom_code_collision_is_rejected_before_writes(target, tmp_path, mode):
    seed(target, custom_bundle())
    incoming = custom_bundle(source=True)
    values = dict(incoming["app_setting"])
    values[b"hierarchy.categories.v1"] = json.dumps({"labels": {
        "C000000001": "다른 의미", "C000000002": "파일 공정"}})
    incoming["app_setting"] = list(values.items())
    before = current(target)
    with pytest.raises(SqlImportError, match="코드 C000000001.*서로 달라"):
        target[2].preview(backup(tmp_path, incoming), mode)
    assert current(target) == before


@pytest.mark.parametrize("mode,expected", [("add", "MEMORY"), ("overwrite", "FOUNDRY")])
def test_matching_line_setting_uses_selected_mode_and_retains_unrelated_lines(target, tmp_path, mode, expected):
    existing, incoming = bundle(), bundle(code=b"EQ-2", group="O3", day=2)
    def lines(area, extra):
        return {"lines": [
            {"site": "화성", "line": "공통 Line", "fab": area, "parent_line": None},
            {"site": "화성", "line": extra, "fab": area, "parent_line": None}]}
    existing["app_setting"] = registry(**{"registered_lines.v1": lines("MEMORY", "대상 전용")})
    incoming["app_setting"] = registry(**{"registered_lines.v1": lines("FOUNDRY", "파일 전용")})
    seed(target, existing)
    service = target[2]
    service.apply(service.preview(backup(tmp_path, incoming), mode))
    saved = {entry["line"]: entry for entry in settings(target)["registered_lines.v1"]["lines"]}
    assert saved["공통 Line"]["fab"] == expected
    assert {"대상 전용", "파일 전용"} <= saved.keys()


@pytest.mark.parametrize("problem", ["missing-category", "missing-area", "missing-parent", "cycle"])
def test_invalid_hierarchy_is_rejected_before_any_write(target, tmp_path, problem):
    incoming = custom_bundle(source=True)
    values = dict(incoming["app_setting"])
    if problem in ("missing-category", "missing-area"):
        del values[b"hierarchy.categories.v1" if problem == "missing-category" else b"hierarchy.areas.v1"]
    else:
        value = json.loads(values[b"registered_lines.v1"])
        if problem == "missing-parent":
            value["lines"][1]["parent_line"] = "없는 Line"
        else:
            value["lines"][0]["parent_line"] = "파일 세부"
        values[b"registered_lines.v1"] = json.dumps(value)
    incoming["app_setting"] = list(values.items())
    with pytest.raises(SqlImportError):
        target[2].preview(backup(tmp_path, incoming), "add")
    assert all(not rows for rows in current(target).values())


def test_add_preserves_target_deleted_default_category(target, tmp_path):
    existing, incoming = bundle(), bundle(code=b"EQ-2", group="O3", day=2)
    existing["app_setting"] = registry(**{"hierarchy.categories.v1": {
        "labels": {"OXIDE": "OXIDE", "KCTC": "KCTC", "HPA": "HPA"}, "deleted": ["POLY"]}})
    incoming["app_setting"] = registry(**{"hierarchy.categories.v1": {"labels": {"POLY": "POLY"}}})
    seed(target, existing)
    service = target[2]
    service.apply(service.preview(backup(tmp_path, incoming), "add"))
    saved = settings(target)["hierarchy.categories.v1"]
    assert "POLY" not in saved["labels"] and saved["deleted"] == ["POLY"]


def test_sql_refuses_a_parent_model_in_another_category(target, tmp_path):
    incoming = bundle()
    incoming["equipment_group"].append((2, "다른 분류의 상위", "POLY", None, 1))
    first = list(incoming["equipment_group"][0])
    first[3] = 2
    incoming["equipment_group"][0] = tuple(first)
    with pytest.raises(SqlImportError, match="상위 설비군.*분류"):
        target[2].preview(backup(tmp_path, incoming), "add")
    assert all(not rows for rows in current(target).values())


def test_overwrite_rejects_default_deletion_that_would_orphan_retained_target_models(target, tmp_path):
    existing = bundle()
    existing["equipment_group"][0] = (1, "Target Poly", "POLY", None, 1)
    seed(target, existing)
    incoming = bundle(code=b"EQ-2", group="O3", day=2)
    incoming["app_setting"] = registry(**{"hierarchy.categories.v1": {
        "labels": {"OXIDE": "OXIDE", "KCTC": "KCTC", "HPA": "HPA"}, "deleted": ["POLY"]}})
    with pytest.raises(SqlImportError, match="분류 코드의 정의"):
        target[2].preview(backup(tmp_path, incoming), "overwrite")


@pytest.mark.parametrize("mode", ["add", "overwrite"])
def test_first_hierarchy_import_into_empty_database_is_idempotent(target, tmp_path, mode):
    path = backup(tmp_path, custom_bundle(source=True))
    service = target[2]
    service.apply(service.preview(path, mode))
    assert service.preview(path, mode).changes == 0


@pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")
def test_mysql_hierarchy_registry_merge_and_alias_remap(schema_database, tmp_path):
    metadata = MetaData()
    metadata.reflect(schema_database.engine)
    mysql_target = (schema_database.engine, metadata.tables,
                    SqlImportService(lambda: Database.from_env(prefix="SETRON_TEST_DB_")))
    test_sql_keeps_both_databases_custom_definitions_and_remaps_aliases(mysql_target, tmp_path, "add")


def test_unknown_area_line_hierarchy_round_trips(target, tmp_path):
    incoming = bundle(line="Unknown-A")
    incoming["app_setting"] = registry(**{"registered_lines.v1": {"lines": [
        {"site": "화성", "line": "Unknown", "fab": None, "parent_line": None},
        {"site": "화성", "line": "Unknown-A", "fab": None, "parent_line": "Unknown"},
    ]}})
    path = backup(tmp_path, incoming)
    service = target[2]
    service.apply(service.preview(path, "add"))
    assert all(entry["fab"] is None for entry in settings(target)["registered_lines.v1"]["lines"])
    assert service.preview(path, "add").changes == 0


@pytest.mark.parametrize("key,value", [
    ("hierarchy.areas.v1", {"labels": {"MEMORY": "FOUNDRY", "FOUNDRY": "다른 이름"}}),
    ("hierarchy.areas.v1", {"labels": {"A000000001": "파운드리", "FOUNDRY": "다른 이름"}}),
    ("hierarchy.categories.v1", {"labels": {"C000000001": "POLY", "POLY": "다른 이름"}}),
])
def test_sql_cannot_bypass_reserved_code_label_guards(target, tmp_path, key, value):
    incoming = bundle()
    incoming["app_setting"] = registry(**{key: value})
    with pytest.raises(SqlImportError, match="예약 이름"):
        target[2].preview(backup(tmp_path, incoming), "add")


def test_floor_alias_does_not_bypass_managed_area_validation(target, tmp_path):
    incoming = custom_bundle(source=True)
    snapshot = list(incoming["equipment_snapshot"][0])
    snapshot[4], snapshot[7] = "파일 세부(3F)", "MEMORY"
    incoming["equipment_snapshot"] = [tuple(snapshot)]
    with pytest.raises(SqlImportError, match="생산 분류가 등록 라인과 다릅니다"):
        target[2].preview(backup(tmp_path, incoming), "add")


def test_parent_reference_must_use_registered_name_without_inventing_an_alias_root(target, tmp_path):
    incoming = bundle()
    incoming["app_setting"] = registry(**{"registered_lines.v1": {"lines": [
        {"site": "화성", "line": "P1", "fab": None, "parent_line": None},
        {"site": "화성", "line": "P1F", "fab": None, "parent_line": "P1(3F)"},
    ]}})
    with pytest.raises(SqlImportError, match="참조 이름이 등록된 이름과 다릅니다"):
        target[2].preview(backup(tmp_path, incoming), "add")


@pytest.mark.parametrize("mode", ["add", "overwrite"])
def test_line_only_hierarchy_round_trips_without_snapshots_or_separate_site_registry(target, tmp_path, mode):
    from setron.features.storage.service import StorageService
    from setron.ui.database_source import load_dashboard_source

    lines = [
        {"site": "빈 등록 사업장", "line": "Line A", "fab": None, "parent_line": None},
        {"site": "빈 등록 사업장", "line": "Detail A", "fab": None, "parent_line": "Line A"},
    ]
    incoming = {"app_setting": registry(**{"registered_lines.v1": {"lines": lines}})}
    path = backup(tmp_path, incoming)
    service = target[2]
    service.apply(service.preview(path, mode))
    saved = current(target)
    assert not saved["equipment_snapshot"] and not saved["equipment_group"]
    assert settings(target)["registered_lines.v1"]["lines"] == lines
    source = load_dashboard_source(StorageService(Database(target[0])), publish_lines=False)
    assert [site.label for site in source.catalog.sites] == ["빈 등록 사업장"]
    assert {line.label for line in source.catalog.lines} == {"Line A", "Detail A"}
    assert service.preview(path, mode).changes == 0
