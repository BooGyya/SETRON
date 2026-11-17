"""BE-011 조회 목록을 실제 SQLite 트랜잭션과 원본 관측으로 검증한다."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime

import pytest

from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.schemas import SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.query_options import LineOption, QueryOptionsService, QueryScope, build_query_options
from setron.features.storage.validation import StorageValidationError

from test_release_revision import all_rows, observation, revision_storage


EARLY, LATE = date(2026, 10, 1), date(2026, 11, 1)


def update_snapshot(storage, snapshot_id, **changes):
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": snapshot_id}, changes)


@pytest.fixture
def stored(revision_storage):
    """동일 설비의 날짜별 라인·모델·PM 구성이 다른 자료를 만든다."""
    storage = revision_storage
    first, _ = observation(storage, day=EARLY, versions=((1, 1, "v1", None), (2, 3, "c1", None)), pms=True)
    update_snapshot(storage, first, line="과거 Line", geometry_type="5각")
    later, _ = observation(storage, day=LATE, versions=((1, 2, "v2", None),), pms=True)
    update_snapshot(storage, later, site="평택", line="새 Line", geometry_type="6각")
    second, _ = observation(storage, day=EARLY, code="EQ-2", versions=((1, 2, "v2", None),))
    update_snapshot(storage, second, site="평택", line=None)
    return storage, first, later, second


def test_dates_only_does_not_select_latest_or_fill_current_values(stored):
    storage, *_ = stored
    service = QueryOptionsService(storage)
    dates = service.read()
    assert dates.dates == (EARLY, LATE)
    assert dates.scope.base_date is None
    assert dates.sites == dates.models == dates.families == dates.matches == ()
    empty = service.read(QueryScope(date(2026, 10, 2)))
    assert empty.dates == dates.dates
    assert empty.sites == empty.lines == empty.model_family_pairs == empty.snapshot_ids == ()


def test_exact_date_uses_snapshot_site_line_and_actual_pm_pairs(stored):
    storage, first, later, second = stored
    service = QueryOptionsService(storage)
    past = service.read(QueryScope(EARLY))
    assert past.snapshot_ids == (first, second)
    assert past.sites == ("평택", "화성")
    assert set(past.lines) == {LineOption("화성", "과거 Line"), LineOption("평택", None)}
    assert past.categories == ("OXIDE", "POLY")
    assert past.model_family_pairs == ((1, 1), (2, 1), (3, 2))
    assert [(model.model_id, model.parent_model_id) for model in past.models] == [(1, None), (2, 1), (3, None)]
    assert past.matches[0].snapshot.geometry_type == "5각"
    current = service.read(QueryScope(LATE))
    assert current.snapshot_ids == (later,)
    assert current.sites == ("평택",) and current.lines == (LineOption("평택", "새 Line"),)
    assert current.model_family_pairs == ((2, 1),)


def test_multiple_selections_are_or_inside_and_across_axes(stored):
    storage, first, _, second = stored
    options = QueryOptionsService(storage).read(QueryScope(
        EARLY, sites=("화성", "평택"), lines=("과거 Line", None),
        categories=("OXIDE",), model_ids=(1, 2), family_ids=(1, 2),
    ))
    assert options.snapshot_ids == (first, second)
    assert options.model_family_pairs == ((1, 1), (2, 1))
    assert options.categories == ("OXIDE",)
    assert options.matches[0].model_family_pairs == ((1, 1),)
    assert len(options.matches[0].snapshot.software) == 2  # 원본 SW와 PM을 잘라내지 않음
    one = QueryOptionsService(storage).read(replace(options.scope, sites=("화성",)))
    assert one.snapshot_ids == (first,) and one.model_family_pairs == ((1, 1),)


def test_mixed_equipment_does_not_create_cross_product_of_models_and_families(stored):
    storage, first, *_ = stored
    service = QueryOptionsService(storage)
    assert service.read(QueryScope(EARLY, sites=("화성",), model_ids=(1,), family_ids=(2,))).matches == ()
    assert service.read(QueryScope(EARLY, sites=("화성",), categories=("OXIDE",), family_ids=(2,))).matches == ()
    proper = service.read(QueryScope(EARLY, model_ids=(3,), family_ids=(2,)))
    assert proper.snapshot_ids == (first,) and proper.model_family_pairs == ((3, 2),)
    assert [model.model_id for model in proper.models] == [3]


def test_unknown_line_and_same_line_name_at_different_sites_remain_distinct(stored):
    storage, first, _, second = stored
    unknown = QueryOptionsService(storage).read(QueryScope(EARLY, lines=(None,)))
    assert unknown.snapshot_ids == (second,)
    assert unknown.lines == (LineOption("평택", None),)
    update_snapshot(storage, first, line="공통 Line")
    update_snapshot(storage, second, line="공통 Line")
    options = QueryOptionsService(storage).read(QueryScope(EARLY, lines=("공통 Line",)))
    assert set(options.lines) == {LineOption("화성", "공통 Line"), LineOption("평택", "공통 Line")}


def test_saved_optional_line_values_round_trip_into_both_filters_without_changing_raw_data(revision_storage):
    """저장 가능한 빈 라인도 목록과 두 필터에서 원문 그대로 다시 사용할 수 있다."""
    storage = revision_storage
    raw_lines = (None, "", "   ", "Line", " Line ", "")
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})["batch_id"]
    saved = EquipmentService(storage).save_snapshots(tuple(
        SnapshotInput(equipment_code=f"OPTIONAL-{number}", base_date=EARLY, site="화성", line=raw,
                      model_name="O2", group_id=1, batch_id=batch,
                      software=(SoftwareInput(family_id=1, version_raw="v1"),))
        for number, raw in enumerate(raw_lines)
    ))
    tables = ("equipment_snapshot", "equipment_version", "equipment_pm", "import_batch")
    before = {name: all_rows(storage, name) for name in tables}
    service = QueryOptionsService(storage)
    options = service.read(QueryScope(EARLY))
    assert options.lines == tuple(LineOption("화성", raw) for raw in (None, "", "   ", " Line ", "Line"))
    assert tuple(match.snapshot.line for match in options.matches) == raw_lines
    for option in options.lines:
        expected = tuple(row.snapshot_id for row in saved if row.line == option.line)
        by_line = service.read(QueryScope(EARLY, lines=(option.line,)))
        by_site_line = service.read(QueryScope(EARLY, site_lines=(option,)))
        assert by_line.snapshot_ids == by_site_line.snapshot_ids == expected
        assert all(match.snapshot.line == option.line for match in by_line.matches)
    assert {name: all_rows(storage, name) for name in tables} == before


@pytest.mark.parametrize("raw_line", ["", "   "])
def test_past_blank_line_is_not_replaced_by_later_line(revision_storage, raw_line):
    storage = revision_storage
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "MANUAL"})["batch_id"]
    value = SnapshotInput(equipment_code="OPTIONAL-HISTORY", base_date=EARLY, site="화성", line=raw_line,
                          model_name="O2", group_id=1, batch_id=batch,
                          software=(SoftwareInput(family_id=1, version_raw="v1"),))
    old, current = EquipmentService(storage).save_snapshots((value, replace(value, base_date=LATE, line="새 Line")))
    service = QueryOptionsService(storage)
    past = service.read(QueryScope(EARLY))
    assert past.lines == (LineOption("화성", raw_line),)
    assert past.snapshot_ids == (old.snapshot_id,)
    assert service.read(QueryScope(EARLY, lines=(raw_line,))).snapshot_ids == (old.snapshot_id,)
    assert service.read(QueryScope(EARLY, site_lines=past.lines)).snapshot_ids == (old.snapshot_id,)
    assert service.read(QueryScope(LATE)).lines == (LineOption("화성", "새 Line"),)
    assert service.read(QueryScope(LATE, lines=(raw_line,))).matches == ()
    assert current.line == "새 Line" and past.matches[0].snapshot.line == raw_line


def test_site_line_pairs_select_exact_combinations_and_multiple_values(stored):
    storage, first, _, second = stored
    update_snapshot(storage, first, line="L1")
    update_snapshot(storage, second, line="L1")
    third, _ = observation(storage, code="EQ-3")
    update_snapshot(storage, third, site="화성", line="L2")
    fourth, _ = observation(storage, code="EQ-4")
    update_snapshot(storage, fourth, site="평택", line="L2")
    service = QueryOptionsService(storage)
    one = service.read(QueryScope(EARLY, site_lines=(LineOption("화성", "L1"),)))
    assert one.snapshot_ids == (first,)
    assert one.lines == (LineOption("화성", "L1"),)
    multiple = service.read(QueryScope(EARLY, site_lines=(LineOption("화성", "L1"), LineOption("평택", "L2"))))
    assert multiple.snapshot_ids == (first, fourth)
    assert second not in multiple.snapshot_ids and third not in multiple.snapshot_ids
    combined = service.read(replace(multiple.scope, lines=("L1",)))
    assert combined.snapshot_ids == (first,)
    assert service.read(replace(multiple.scope, sites=("다른 사업장",))).matches == ()


def test_site_line_selection_preserves_unknown_and_copies_canonical_list(stored):
    storage, _, _, second = stored
    values = [LineOption("평택", None), LineOption("화성", "L1"), LineOption("평택", None)]
    scope = QueryScope(EARLY, site_lines=values)
    values.clear()
    assert scope.site_lines == (LineOption("평택", None), LineOption("화성", "L1"))
    assert QueryOptionsService(storage).read(scope).snapshot_ids == (second,)


def test_c3_ui_is_separate_but_geometry_does_not_create_a_family(revision_storage):
    storage = revision_storage
    with storage.transaction() as tx:
        tx.insert("version_family", {"family_id": 3, "group_id": 3,
                                     "family_name": "C3 통합UI", "ui_type": "INTEGRATED"})
    for code, family, shape in (("C3-5", 2, "5각"), ("C3-6", 2, "6각"), ("C3-new", 3, "6각")):
        snapshot_id, _ = observation(storage, code=code, versions=((family, 3, "v1", None),))
        update_snapshot(storage, snapshot_id, geometry_type=shape)
    options = QueryOptionsService(storage).read(QueryScope(EARLY))
    assert options.model_family_pairs == ((3, 2), (3, 3))
    assert [(family.family_id, family.ui_type) for family in options.families] == [(2, "LEGACY"), (3, "INTEGRATED")]
    assert len(options.matches) == 3


def test_query_does_not_change_saved_records_or_create_settings(stored):
    storage, *_ = stored
    tables = ("equipment_snapshot", "equipment_version", "equipment_pm", "app_setting")
    before = {table: all_rows(storage, table) for table in tables}
    options = QueryOptionsService(storage).read(QueryScope(EARLY))
    with pytest.raises(FrozenInstanceError):
        options.scope.base_date = LATE
    assert {table: all_rows(storage, table) for table in tables} == before


def test_read_in_shares_callers_transaction_and_sees_pending_values(stored):
    storage, first, *_ = stored
    service = QueryOptionsService(storage)
    with storage.transaction() as tx:
        tx.update("equipment_snapshot", {"snapshot_id": first}, {"line": "같은 저장 묶음"})
        options = service.read_in(tx, QueryScope(EARLY, sites=("화성",)))
        assert options.lines == (LineOption("화성", "같은 저장 묶음"),)
        assert service.read_in(tx).dates == (EARLY, LATE)
    assert service.read(QueryScope(EARLY, sites=("화성",))).lines == options.lines


@pytest.mark.parametrize("changes", [
    {"base_date": "2026-10-01"}, {"base_date": datetime(2026, 10, 1)},
    {"sites": "화성"}, {"sites": [""]}, {"lines": [False]}, {"lines": [12]},
    {"lines": ["L" * 51]}, {"categories": ["Oxide"]},
    {"model_ids": [True]}, {"model_ids": [0]}, {"family_ids": ["1"]}, {"family_ids": [-1]},
    {"site_lines": "화성/L1"}, {"site_lines": [("화성", "L1")]}, {"site_lines": [None]},
])
def test_scope_rejects_ambiguous_or_invalid_inputs(changes):
    with pytest.raises(StorageValidationError):
        QueryScope(**changes)


def test_scope_copies_lists_and_deduplicates_without_changing_values():
    models = [2, 1, 2]
    scope = QueryScope(EARLY, model_ids=models, lines=[" Line ", None])
    models.append(3)
    assert scope.model_ids == (1, 2) and scope.lines == (None, " Line ")


@pytest.mark.parametrize("site,line", [(None, "L1"), ("", "L1"), ("화성", False), ("화성", 12), ("화성", "L" * 51)])
def test_line_option_rejects_invalid_site_or_line(site, line):
    with pytest.raises(StorageValidationError):
        LineOption(site, line)


def test_no_records_and_unknown_filter_values_return_empty_lists(revision_storage):
    service = QueryOptionsService(revision_storage)
    assert service.read().dates == ()
    assert service.read(QueryScope(EARLY, sites=("자료 없는 사업장",), model_ids=(999,))).matches == ()


def test_pure_builder_rejects_duplicate_snapshots_or_unregistered_pm(stored):
    storage, *_ = stored
    with storage.transaction() as tx:
        repository = EquipmentRepository()
        groups = repository.rows(tx, "equipment_group")
        families = repository.rows(tx, "version_family")
    snapshot = QueryOptionsService(storage).read(QueryScope(EARLY)).matches[0].snapshot
    with pytest.raises(StorageValidationError, match="중복"):
        build_query_options((snapshot, snapshot), groups, families, QueryScope(EARLY))
    with pytest.raises(StorageValidationError, match="모델"):
        build_query_options((snapshot,), [group for group in groups if group["group_id"] != 3], families, QueryScope(EARLY))
    assert build_query_options((snapshot,), groups, families, QueryScope(LATE)).matches == ()
