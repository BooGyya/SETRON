"""Jira 저장 열의 실제 존재와 NULL을 UI·검색·관리 내보내기에서 동일하게 읽는다."""

from contextlib import closing
from copy import deepcopy

import pytest

from setron.features.export.management import build_management_workbook
from setron.features.item.schemas import Item
from setron.features.storage.repository import StorageRepository
from setron.ui.database_source import load_dashboard_source
from setron.ui.item_data import ItemManagerData, item_data_from_source
from setron.ui.item_original import JIRA_EMPTY, JIRA_FOUND
from setron.ui.version_data import ItemEntry, SourceReference, VersionCatalog

from test_export_jira import jira_source
from test_export_mentoring6 import records
from test_release_revision import all_rows, revision_storage  # noqa: F401


@pytest.mark.parametrize("layout,key_value,code_value,expected", [
    ("key", None, None, ()),
    ("key", "ETOJ-701, ETOJ-702", None, ("ETOJ-701", "ETOJ-702")),
    ("code", None, None, ()),
    ("code", None, "ETOJ-801; ETOJ-802", ("ETOJ-801", "ETOJ-802")),
    ("dual", "ETOJ-701", None, ()),
    ("dual", "ETOJ-701", "ETOJ-801", ("ETOJ-801",)),
    ("none", None, None, ("ETOJ-101", "ETOJ-102", "ABC-77")),
])
def test_physical_jira_columns_and_explicit_null_have_the_same_ui_search_and_export_meaning(
        revision_storage, layout, key_value, code_value, expected):
    storage = revision_storage
    _, item_id, _ = jira_source(storage)
    assert storage.database.engine.dialect.name == "sqlite"
    # 이 격리된 메모리 DB만 실제 구형/신형 열 구성으로 바꾼다. 앱의 자동 이관은 호출하지 않는다.
    wanted = {"key": {"jira_key"}, "code": {"jira_code"}, "dual": {"jira_key", "jira_code"}, "none": set()}[layout]
    current = set(storage.repository.table("item").c.keys())
    with storage.database.engine.begin() as connection:
        for field in ("jira_key", "jira_code"):
            if field in wanted and field not in current:
                connection.exec_driver_sql(f"ALTER TABLE item ADD COLUMN {field} VARCHAR(200)")
            elif field not in wanted and field in current:
                connection.exec_driver_sql(f"ALTER TABLE item DROP COLUMN {field}")
        if layout in {"key", "dual"}:
            connection.exec_driver_sql("UPDATE item SET jira_key = ? WHERE item_id = ?", (key_value, item_id))
        if layout in {"code", "dual"}:
            connection.exec_driver_sql("UPDATE item SET jira_code = ? WHERE item_id = ?", (code_value, item_id))
        storage.repository = StorageRepository(connection)
    before = {name: deepcopy(all_rows(storage, name)) for name in ("item", "import_batch", "release_version_item")}

    source = load_dashboard_source(storage)
    entry = source.version_catalog.items[0]
    stored = layout != "none"
    assert entry.jira_code_known is stored
    assert entry.item.jira_code == (code_value if layout in {"code", "dual"} else key_value)
    data = item_data_from_source(source.version_catalog, source)
    jira = data.jira([item_id])[item_id]
    assert jira.codes == expected and jira.stored is stored
    assert jira.status == (JIRA_FOUND if expected else JIRA_EMPTY)
    for code in expected:
        assert [found.item.item_id for found in data.search(code.lower())] == [item_id]
    if stored:
        assert data.search("ETOJ-101") == ()  # 확정 NULL/수정값을 보관 원본으로 되살리지 않는다.

    exported = ((code_value if layout in {"code", "dual"} else key_value) or "") if stored else ", ".join(expected)
    with closing(build_management_workbook(storage)) as book:
        assert records(book["릴리즈노트"])[0]["Jira 코드"] == exported
    assert {name: all_rows(storage, name) for name in before} == before


@pytest.mark.parametrize("field", ["jira_code", "jira_key"])
def test_saved_multiple_codes_keep_both_canonical_and_legacy_source_evidence(field):
    reference = SourceReference(field, sheet="노트", cells="E4", batch_id=7)
    entry = ItemEntry(Item(1, 1, "변경 내용", "ETOJ-1, ETOJ-2"), (reference,), jira_code_known=True)
    data = ItemManagerData(VersionCatalog((), (), (entry,)))
    jira = data.jira()[1]
    assert jira.codes == ("ETOJ-1", "ETOJ-2") and jira.stored
    assert jira.sources == ("입력 작업 7 · 노트 · E4",)
    assert [found.item.item_id for found in data.search("etoj-2")] == [1]
