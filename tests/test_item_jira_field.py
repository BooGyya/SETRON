"""JIRA 코드의 원본 추출·정식 저장·조회·검색과 이전 화면 자료 호환을 검증한다."""

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from setron.features.item.jira import extract_jira_from_source, extract_jira_key
from setron.features.item.schemas import Item
from setron.features.management import ManagementService, RecordEdit
from setron.features.release.schemas import ItemInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.ui.item_data import ItemManagerData
from setron.ui.item_original import JIRA_EMPTY, JIRA_FOUND, JIRA_NO_ORIGINAL, OriginalItemRow
from setron.ui.version_data import ItemEntry, VersionCatalog

from test_release_import_integration import import_request, reviewed_request
from test_release_revision import revision_storage  # noqa: F401


@pytest.mark.parametrize("text,expected", [(None, None), ("일반 내용", None),
    ("ETOJ-3257", "ETOJ-3257"), ("수정 (ETOJ-3257)", "ETOJ-3257"),
    ("ETOJ-3257 / ETOJ-3257", "ETOJ-3257")])
def test_extracts_only_real_single_code(text, expected):
    assert extract_jira_key(text) == expected


def test_multiple_different_codes_are_rejected():
    with pytest.raises(ValueError, match="여러 개"):
        extract_jira_key("ETOJ-1, ETOJ-2")


@pytest.mark.parametrize("shape", ["flat", "nested", "request"])
def test_header_cell_evidence_supports_each_original_container_shape(shape):
    sheet = {"D2": "Update내용", "E2": "REMARK", "D4": "변경 내용", "E4": "ETOJ-4120"}
    raw = {"Release List": sheet} if shape == "flat" else {"sheets": {"Release List": sheet}} if shape == "nested" else {"sheet": "Release List", "cells": sheet}
    evidence = extract_jira_from_source(raw, {"content": {"sheet": "Release List", "cells": ["D4"]}})
    assert evidence.jira_key == "ETOJ-4120"
    assert evidence.source_reference == {"jira_key": {"sheet": "Release List", "cells": ["E4"]}}


def test_all_original_items_extract_the_exact_987_codes_with_single_source_evidence():
    catalog = json.loads((Path(__file__).resolve().parents[1] / "database/demo_release_catalog.json").read_text(encoding="utf-8"))
    counts, keys = Counter(), []
    for family, data in catalog["families"].items():
        sheets = catalog["workbooks"][data["workbook"]]["sheets"]
        for item in data["items"]:
            evidence = extract_jira_from_source(sheets, {"content": {"sheet": data["sheet"], "cells": [item["cell"]]}}, content=item["content"])
            if evidence.jira_key:
                keys.append(evidence.jira_key)
                counts[family] += 1
                locations = evidence.source_reference["jira_key"]
                for location in locations if isinstance(locations, list) else [locations]:
                    assert all(evidence.jira_key in sheets[location["sheet"]][cell] for cell in location["cells"])
    assert counts == {"c3_legacy": 65, "c3": 87, "c3_foundry": 38, "c4": 171, "o2": 83, "o2_foundry": 36, "o5": 507}
    assert len(keys) == 987 and len(set(keys)) == 945


def test_saved_code_is_shown_and_searchable_without_legacy_original_rows():
    entries = (ItemEntry(Item(1, 7, "일반 수정", "ETOJ-4120"), jira_key_known=True),
               ItemEntry(Item(2, 8, "추정 항목"), jira_key_known=True), ItemEntry(Item(3, 9, "이전 자료")))
    data = ItemManagerData(VersionCatalog((), (), entries))
    assert [entry.item.item_id for entry in data.search("etoj-4120")] == [1]
    jira = data.jira()
    assert jira[1].codes == ("ETOJ-4120",) and jira[1].status == JIRA_FOUND
    assert jira[2].status == JIRA_EMPTY and "저장된" in jira[2].tooltip
    assert jira[3].status == JIRA_NO_ORIGINAL


def test_explicitly_cleared_saved_code_does_not_reappear_from_preserved_original():
    original = OriginalItemRow(1, 7, "노트", 2, ("내용", "JIRA"), ("변경", "ETOJ-4120"))
    data = ItemManagerData(VersionCatalog((), (), (ItemEntry(Item(1, 7, "변경"), jira_key_known=True),),
                                        original_rows=(original,)))
    assert data.jira()[1].codes == () and data.jira()[1].status == JIRA_EMPTY
    assert data.search("ETOJ-4120") == ()


def test_excel_item_registration_and_official_edits_persist_jira(revision_storage):
    storage = revision_storage
    with storage.transaction() as tx:
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": {"노트": {
            "A1": "내용", "B1": "JIRA", "A2": "변경 내용", "B2": "ETOJ-4120"}}})
    service = ReleaseService(storage)
    detail = service.register_release(ReleaseInput(family_id=1, version_raw="v1"), (
        ItemInput(content="변경 내용", batch_id=batch["batch_id"], source_reference={"content": {"sheet": "노트", "cells": ["A2"]}}),))
    item = detail.items[0].item.current
    assert item["jira_key"] == "ETOJ-4120" and item["source_reference"]["jira_key"]["cells"] == ["B2"]
    edited = service.update_item(item["item_id"], {"jira_key": "ETOJ-9999"})
    assert edited.current["jira_key"] == "ETOJ-9999" and "jira_key" in edited.current["edited_fields"]
    management = ManagementService(storage)
    current = management.get_record("item", {"item_id": item["item_id"]})
    management.save((RecordEdit(table="item", key=current.key, revision=current.revision, changes={"jira_key": None}),))
    assert service.get_item(item["item_id"]).current["jira_key"] is None


@pytest.mark.parametrize("complete_note", [False, True])
def test_excel_save_extracts_jira_before_both_new_and_complete_note_paths(revision_storage, complete_note):
    from setron.features.import_save.service import DatabaseImportSaver
    request = import_request(revision_storage, confirmed=complete_note)
    cells = {key[0] + "2": value for key, value in request.raw_data["cells"].items()}
    cells.update(D1="Update내용", G1="JIRA", G2="ETOJ-3257")
    row = request.rows[0]
    sources = {field: {"sheet": location["sheet"], "cells": [cell[0] + "2" for cell in location["cells"]]}
               for field, location in row.source_reference.items()}
    request = replace(request, raw_data={"sheet": "노트", "cells": cells}, rows=(replace(row, source_reference=sources),))
    saver = DatabaseImportSaver(revision_storage)
    if complete_note:
        request = reviewed_request(saver, request)
    saver.save(request)
    with revision_storage.transaction() as tx:
        from setron.features.equipment.repository import EquipmentRepository
        items = EquipmentRepository().rows(tx, "item")
    assert len(items) == 1 and items[0]["jira_key"] == "ETOJ-3257"
