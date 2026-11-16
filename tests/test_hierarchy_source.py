"""등록한 임의 계층이 공통 조회·히트맵·Replay·보고서에 연결되는지 검증한다."""

from datetime import date


from setron.features.data_admin.service import LINES_KEY
from setron.features.hierarchy import AREAS_KEY, CATEGORIES_KEY
from setron.features.ai.schemas import QueryConditions
from setron.features.chat import recognize
from setron.features.query.service import resolve_conditions, select_records
from setron.ui.chat_data import chat_catalog
from setron.ui.database_source import database_catalog, group_keys, load_dashboard_source
from setron.ui.heatmap_data import equipment_tiles
from setron.ui.heatmap_grouping import build_heatmap_groups
from setron.ui.query_state import AppliedQuery, QuerySelection
from setron.ui.replay_data import ReplayRequest, load_replay, replay_trends
from setron.ui.report_visual_data import grouped_report_heatmaps, report_visuals

from test_management_service import observe
from test_release_revision import release, revision_storage  # noqa: F401


ALL = AppliedQuery(QuerySelection(), 1, 0, "전체")


def setting(tx, key, value):
    tx.insert("app_setting", {"setting_key": key, "setting_value": value})


def configured_source(storage, *, child="검사 A", parent="상위 라인"):
    release_id = release(storage)
    for day in (date(2026, 10, 1), date(2026, 10, 2)):
        saved = observe(storage, release_id, day=day)
        with storage.transaction() as tx:
            tx.update("equipment_snapshot", {"snapshot_id": saved.snapshot_id},
                      {"line": child, "production_area": "A000000001"})
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1},
                  {"group_name": "검사 모델", "category": "C000000001"})
        tx.update("equipment_group", {"group_id": 2}, {"category": "C000000001"})
        setting(tx, CATEGORIES_KEY, {"labels": {"C000000001": "신규 공정"}})
        setting(tx, AREAS_KEY, {"labels": {"A000000001": "시험 구역"}})
        setting(tx, LINES_KEY, {"lines": [
            {"site": "화성", "line": parent, "fab": "A000000001", "parent_line": None},
            {"site": "화성", "line": child, "fab": "A000000001", "parent_line": parent},
        ]})
    return load_dashboard_source(storage, publish_lines=False)


def test_custom_hierarchy_drives_query_heatmap_replay_and_report(revision_storage):
    source = configured_source(revision_storage)
    parent = next(line for line in source.catalog.lines if line.label == "상위 라인")
    child = next(line for line in source.catalog.lines if line.label == "검사 A")
    assert child.parent_key == parent.key
    assert next(area for area in source.catalog.fabs if area.key == "A000000001").label == "시험 구역"
    group = next(group for group in source.catalog.groups if group.key == "group-1")
    assert (group.label, group.category, group.category_label) == ("검사 모델", "C000000001", "신규 공정")
    assert source.bindings[group.key] == (1, 2)
    assert [site.label for site in source.catalog.sites] == ["화성"]
    for line in (parent, child):
        selected = AppliedQuery(QuerySelection(line_key=line.key), 2, 0, line.label)
        assert source.snapshot(selected).summary.equipment_count == 1
    snapshot = source.snapshot(ALL)
    tiles = equipment_tiles(snapshot, source)
    assert (tiles[0].parent_line_label, tiles[0].subline_label) == ("상위 라인", "검사 A")
    groups = ((group.key, group.label, (tiles[0].equipment_id,)),)
    assert [node.title for node in build_heatmap_groups(tiles, groups, mode="line")] == ["상위 라인"]
    assert [node.title for node in build_heatmap_groups(tiles, groups, mode="subline")] == ["검사 A"]
    data = load_replay(source, ReplayRequest(date(2026, 10, 1), date(2026, 10, 2), ALL))
    assert [trend.label for trend in replay_trends(data, grouping="subline")] == ["검사 A"]
    assert len(data.frames) == 2
    panels = report_visuals(data.source, data.frames, data.frames[-1])[1]
    grouped = grouped_report_heatmaps(data.source, data.frames[-1], "subline", panels)
    assert [panel.group for panel in grouped] == ["검사 A"]
    assert data.source.area_labels["A000000001"] == "시험 구역"
    catalog = chat_catalog(source)
    query = resolve_conditions(QueryConditions(sites=("화성",), lines=("상위 라인",)), catalog)
    assert query.ready and len(select_records(source.records, query, catalog)) == 1
    assert recognize("시험 구역 설비 보여줘", catalog).areas == ("A000000001",)
    assert next(group for group in catalog.groups if group.group_id == 1).model_name == "검사 모델"


def test_explicit_root_overrides_legacy_subline_in_all_views(revision_storage):
    source = configured_source(revision_storage, child="P1F")
    with revision_storage.transaction() as tx:
        tx.update("app_setting", {"setting_key": LINES_KEY}, {"setting_value": {"lines": [
            {"site": "화성", "line": "P1F", "fab": "A000000001", "parent_line": None},
        ]}})
    source = load_dashboard_source(revision_storage, publish_lines=False)
    tile = equipment_tiles(source.snapshot(ALL), source)[0]
    assert tile.hierarchy_configured and tile.parent_line_label == "P1F" and tile.subline_label is None
    assert build_heatmap_groups((tile,), (), mode="subline") == ()
    assert [node.title for node in build_heatmap_groups((tile,), (), mode="line")] == ["P1F"]
    assert all(line.label != "P1" for line in source.catalog.lines)


def test_registered_model_parent_takes_precedence_over_legacy_name():
    groups = [
        {"group_id": 1, "group_name": "신규 설비군", "parent_group_id": None},
        {"group_id": 2, "group_name": "M-C4", "parent_group_id": 1},
    ]
    assert group_keys(groups) == {1: "group-1", 2: "group-1"}


def test_ai_parent_expansion_is_site_specific_and_leaf_queries_stay_exact(revision_storage):
    from dataclasses import replace

    source = configured_source(revision_storage)
    catalog = chat_catalog(source)
    record = source.records[-1]
    unrelated = replace(record, equipment_id=100, equipment_code="OTHER", site="평택")
    records = (record, unrelated)
    parent = resolve_conditions(QueryConditions(lines=("상위 라인",)), catalog)
    child = resolve_conditions(QueryConditions(lines=("검사 A",)), catalog)
    assert select_records(records, parent, catalog) == (record,)
    assert select_records(records, child, catalog) == records


def test_managed_line_area_is_authoritative_for_older_inconsistent_rows(revision_storage):
    from setron.features.chat.answers import _scope
    from setron.ui.chat_data import ChatService

    source = configured_source(revision_storage)
    with revision_storage.transaction() as tx:
        snapshot = next(iter(tx.session.execute(tx.repository.table("equipment_snapshot").select()).mappings()))
        tx.update("equipment_snapshot", {"snapshot_id": snapshot["snapshot_id"]}, {"production_area": "MEMORY"})
    source = load_dashboard_source(revision_storage, publish_lines=False)
    assert all(source.location_for(record).fab_key == "A000000001" for record in source.records)
    assert all(record.production_area == "A000000001" for record in source.records)
    service = ChatService(source, lambda: {})
    scope = _scope(recognize("시험 구역 설비 보여줘", chat_catalog(source)), service.data())
    assert {record.production_area for record in scope.records} == {"A000000001"}


def test_managed_floor_aliases_share_leaf_filters_without_changing_raw_values(revision_storage):
    source = configured_source(revision_storage, child="P1F", parent="P1")
    with revision_storage.transaction() as tx:
        table = tx.repository.table("equipment_snapshot")
        for index, snapshot in enumerate(tx.session.execute(table.select()).mappings()):
            tx.update("equipment_snapshot", {"snapshot_id": snapshot["snapshot_id"]},
                      {"line": ("p1f(3F)", "P1F(7f)")[index], "production_area": "MEMORY"})
    source = load_dashboard_source(revision_storage, publish_lines=False)
    assert {record.line for record in source.records} == {"p1f(3F)", "P1F(7f)"}
    assert {line.label for line in source.catalog.lines} == {"P1", "P1F"}
    for label in ("P1", "P1F"):
        option = next(line for line in source.catalog.lines if line.label == label)
        selected = source.snapshot(AppliedQuery(QuerySelection(line_key=option.key), 2, 0, label))
        assert selected.summary.equipment_count == 1
        catalog = chat_catalog(source)
        query = resolve_conditions(QueryConditions(lines=(label,)), catalog)
        assert len(select_records(source.records, query, catalog)) == 1


def test_builtin_renamed_group_keeps_its_existing_key():
    groups = [{"group_id": 1, "group_name": "새 설비군 이름", "parent_group_id": None}]
    assert group_keys(groups, {"M-C3": 1}) == {1: "c3"}


def test_registered_line_keeps_its_site_without_observations_or_separate_site_registry():
    catalog = database_catalog((), model_names=(), extra_lines=(
        {"site": "빈 등록 사업장", "line": "Line A", "fab": "MEMORY", "parent_line": None},
    ))
    assert [(site.key, site.label) for site in catalog.sites] == [("빈 등록 사업장", "빈 등록 사업장")]
    assert [(line.label, line.site_key) for line in catalog.lines] == [("Line A", "빈 등록 사업장")]
