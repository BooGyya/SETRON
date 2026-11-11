"""설비현황표·릴리즈노트의 시트 제한과 자료 보존을 SQLite로 검증한다."""

from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime

from openpyxl import load_workbook

from setron.features.export.management import build_management_workbook, save_management_summary
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import ItemLinkInput
from setron.features.release.service import ReleaseService
from test_management_db_source import connected
from test_release_revision import all_rows, observation, release, revision_storage  # noqa: F401


def test_empty_export_has_exactly_two_visible_sheets_with_headers(revision_storage, tmp_path):
    path = tmp_path / "빈자료.xlsx"
    result = save_management_summary(revision_storage, path)
    assert result.saved
    assert result.message == "설비현황표 0행 · 릴리즈노트 0행"
    with closing(load_workbook(path)) as book:
        assert book.sheetnames == ["설비현황표", "릴리즈노트"]
        for sheet in book:
            assert sheet.sheet_state == "visible" and sheet.max_row == 1
            assert all(cell.value for cell in sheet[1])


def test_saved_two_sheet_export_preserves_pms_versions_items_and_literal_text(revision_storage, tmp_path):
    storage = revision_storage
    release_id, item_id, snapshot, _ = connected(storage)
    formula = '=HYPERLINK("https://example.invalid", "개선 내용")'
    with storage.transaction() as tx:
        tx.update("item", {"item_id": item_id}, {"content": formula})
    observation(storage, code="PM-설비", versions=((1, 1, "v1", release_id), (2, 3, "v3", None)), pms=True)
    path = tmp_path / "설비와릴리즈.xlsx"
    result = save_management_summary(storage, path)
    assert result.saved
    with closing(load_workbook(path)) as book:
        assert book.sheetnames == ["설비현황표", "릴리즈노트"]
        equipment = list(book["설비현황표"].iter_rows(min_row=2, values_only=True))
        assert any(row[0] == "EQ-1" and row[1].date() == snapshot.base_date for row in equipment)
        pm_rows = [row for row in equipment if row[0] == "PM-설비"]
        assert sorted((row[10], row[11], row[13], row[14]) for row in pm_rows) == [
            ("PM1", "M-O2", "v1", "v1"), ("PM2", "C3", "v3", "미등록")]
        note = next(row for row in book["릴리즈노트"].iter_rows(min_row=2) if row[9].value == formula)
        assert [cell.value for cell in note[:2]] == ["M-O2", "v1"]
        assert [cell.value for cell in note[8:13]] == ["7", formula, "미적용", "전체 모델", None]
        assert note[9].data_type == "s"
        assert not any(cell.data_type == "f" for sheet in book for row in sheet for cell in row)


def test_release_notes_preserve_multiple_items_empty_releases_and_unlinked_items(revision_storage):
    storage = revision_storage
    _, item_id, _, _ = connected(storage)
    second_id = release(storage, raw="v2", order=2)
    release(storage, raw="v3", order=3)
    item = all_rows(storage, "item")[0]
    with storage.transaction() as tx:
        second_item = tx.insert("item", {"content": "추가 Item", "item_no": 8,
                                        "batch_id": item["batch_id"], "edited_fields": []})
        tx.insert("item", {"content": "아직 연결 전", "item_no": 9,
                           "batch_id": item["batch_id"], "edited_fields": []})
    ReleaseService(storage).save_item_links(second_id, (
        ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED, target_scope=TargetScope.ALL),
        ItemLinkInput(item_id=second_item["item_id"], item_state=ItemState.UNKNOWN,
                      target_scope=TargetScope.LISTED, target_group_ids=(1, 2)),
    ))
    with closing(build_management_workbook(storage)) as book:
        rows = list(book["릴리즈노트"].iter_rows(min_row=2, values_only=True))
        assert Counter(row[1] for row in rows) == {"v1": 1, "v2": 2, "v3": 1, "": 1}
        listed = next(row for row in rows if row[9] == "추가 Item")
        assert listed[8:13] == ("8", "추가 Item", "미확인", "지정 모델", "M-O2, O2 Plus")
        assert next(row for row in rows if row[1] == "v3")[8:14] == ("", "", "", "", "", "")
        assert next(row for row in rows if row[1] == "")[8:10] == ("9", "아직 연결 전")


def test_export_reads_one_transaction_without_changing_saved_data(revision_storage):
    storage = revision_storage
    connected(storage)
    before = {table: all_rows(storage, table) for table in ("equipment_snapshot", "release_version", "item")}

    class CountedStorage:
        count = 0

        @contextmanager
        def transaction(self):
            self.count += 1
            with storage.transaction() as tx:
                yield tx

    counted = CountedStorage()
    created = datetime(2026, 11, 11, 10, 0)
    with closing(build_management_workbook(counted, now=lambda: created)) as book:
        assert counted.count == 1 and book.properties.created == created
    assert {table: all_rows(storage, table) for table in before} == before
