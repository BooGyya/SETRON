"""공유 노트 읽기 캐시의 판정 동등성·최신성·조회 수 검증."""

from copy import deepcopy

import pytest
from sqlalchemy import event

from setron.features.equipment.repository import EquipmentRepository
from setron.features.release.shared_models import SharedNoteItemService, read_item_model_marks
from setron.features.storage.validation import StorageValidationError
from setron.features.targets import TargetService
from setron.infrastructure.excel_reader import SheetData
from setron.ui.database_source import _TargetReadView, load_dashboard_source

from test_release_revision import observation, revision_storage  # noqa: F401
from test_target_shared_marks import catalog, pair, save_release  # noqa: F401


def _view(tx):
    repository = EquipmentRepository()
    return _TargetReadView(tx, {name: repository.rows(tx, name) for name in (
        "equipment_group", "version_family", "release_version", "release_version_item", "item",
    )})


def _read_both(storage, key, selected):
    with storage.transaction() as tx:
        expected = TargetService(None).read_in(tx, (key,), {key: selected})
        actual = TargetService(None).read_in(_view(tx), (key,), {key: selected})
    assert actual == expected
    return actual.pairs[0]


def test_target_read_view_returns_independent_nested_evidence(catalog):
    release_id, item_ids, _ = save_release(catalog, "v1", 1, (("X", "O", "X"),))
    setting_key = {"setting_key": f"shared_note_item_models.v1:{release_id}:{item_ids[0]}"}
    with catalog[0].transaction() as tx:
        view = _view(tx)
        expected = deepcopy(tx.get("app_setting", setting_key))
        first = view.get("app_setting", setting_key)
        first["setting_value"]["entries"][1]["mark"]["mark_raw"] = "X"
        assert view.get("app_setting", setting_key) == expected
        assert tx.get("app_setting", setting_key) == expected
        assert not any(hasattr(view, method) for method in ("insert", "update", "delete_release_item"))


@pytest.mark.parametrize("change", ["content", "jira", "link", "release", "model", "last_o"])
def test_new_read_rechecks_changed_shared_note_evidence(catalog, change):
    storage = catalog[0]
    release_id, item_ids, _ = save_release(catalog, "v1", 1, (("X", "O", "X"),))
    key = pair(catalog, "M-C4 DFB")
    assert _read_both(storage, key, release_id).selection_valid
    if change == "last_o":
        sheet = SheetData("표식 검증.xlsx", "Release List", 2, 7,
                          [["NormalC4", "DFB", "DEX"], ["X", "X", "X"]])
        marks = read_item_model_marks(sheet, note_model="M-C4", header_row=2, item_row=3)
        SharedNoteItemService(storage).save(release_id, item_ids[0], marks)
    else:
        with storage.transaction() as tx:
            if change == "content":
                tx.update("item", {"item_id": item_ids[0]}, {"content": "근거 재확인이 필요한 변경"})
            elif change == "jira":
                tx.update("item", {"item_id": item_ids[0]}, {"jira_key": "ETOJ-77"})
            elif change == "link":
                tx.update("release_version_item", {"item_id": item_ids[0], "release_version_id": release_id},
                          {"item_state": "NOT_APPLIED"})
            elif change == "release":
                tx.update("release_version", {"release_version_id": release_id}, {"version_raw": "v1 변경"})
            else:
                tx.update("equipment_group", {"group_id": key[0]}, {"analysis_enabled": False})
    actual = _read_both(storage, key, release_id)
    assert actual.selected_release_id == release_id
    assert not actual.selection_valid and not actual.candidates


def test_more_shared_item_evidence_does_not_add_selects(catalog):
    storage = catalog[0]
    release_id, _, _ = save_release(catalog, "v1", 1, (("X", "O", "X"),))
    key = pair(catalog, "M-C4 DFB")

    def count_read():
        statements = []

        def counted(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        with storage.transaction() as tx:
            connection = tx.session.connection()
            event.listen(connection, "before_cursor_execute", counted)
            try:
                result = TargetService(None).read_in(_view(tx), (key,), {key: release_id})
            finally:
                event.remove(connection, "before_cursor_execute", counted)
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert result.pairs[0].selection_valid
        return len(statements)

    single = count_read()
    save_release(catalog, "v2", 2, tuple(("O", "O", "X") for _ in range(24)))
    assert count_read() == single


def test_empty_observations_do_not_read_unused_shared_note_json(revision_storage):
    # 관측 쌍이 없으면 이 접두사 자료는 원래 N 후보 읽기의 검증 범위가 아니다.
    with revision_storage.transaction() as tx:
        table = tx.repository.table("app_setting")
        tx.session.execute(table.insert().values(
            setting_key=b"shared_note_item_models.v1:999:999", setting_value="{broken-json",
        ))
    source = load_dashboard_source(revision_storage, publish_lines=False)
    assert source.records == ()
    assert source.version_catalog.items == ()


def test_nonempty_read_preserves_existing_unused_shared_json_validation(revision_storage):
    # 기존 공유 노트 후보 읽기도 요청 쌍이 있으면 접두사 범위 전체의 JSON을 검증한다.
    observation(revision_storage)
    with revision_storage.transaction() as tx:
        table = tx.repository.table("app_setting")
        tx.session.execute(table.insert().values(
            setting_key=b"shared_note_item_models.v1:999:999", setting_value="{broken-json",
        ))
    failures = []
    for cached in (False, True):
        with pytest.raises(StorageValidationError) as failure:
            with revision_storage.transaction() as tx:
                TargetService(None).read_in(_view(tx) if cached else tx, ((1, 1),), {(1, 1): None})
        failures.append(str(failure.value))
    assert failures[0] == failures[1]
