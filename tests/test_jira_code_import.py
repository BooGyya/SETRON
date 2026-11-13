"""복수 Jira가 일반·전체노트 저장에서 canonical 값/근거로 보존되는지 검증한다."""

from dataclasses import replace

import pytest

from setron.features.equipment.repository import EquipmentRepository
from setron.features.import_save.service import DatabaseImportSaver

from test_release_import_integration import import_request, reviewed_request
from test_release_revision import revision_storage  # noqa: F401


def multiple_request(storage, *, confirmed=False):
    request = import_request(storage, confirmed=confirmed)
    cells = {key[0] + "2": value for key, value in request.raw_data["cells"].items()}
    cells.update(D1="Update내용", G1="JIRA", G2="ETOJ-1; ETOJ-2\nETOJ-1")
    row = request.rows[0]
    sources = {field: {"sheet": location["sheet"], "cells": [cell[0] + "2" for cell in location["cells"]]}
               for field, location in row.source_reference.items()}
    return replace(request, raw_data={"sheet": "노트", "cells": cells},
                   rows=(replace(row, source_reference=sources),))


@pytest.mark.parametrize("complete_note", [False, True])
def test_multiple_source_codes_are_saved_with_canonical_evidence(revision_storage, complete_note):
    request = multiple_request(revision_storage, confirmed=complete_note)
    saver = DatabaseImportSaver(revision_storage)
    if complete_note:
        request = reviewed_request(saver, request)
    saver.save(request)
    with revision_storage.transaction() as tx:
        items = EquipmentRepository().rows(tx, "item")
    assert len(items) == 1 and items[0]["jira_code"] == "ETOJ-1, ETOJ-2"
    assert "jira_key" not in items[0]
    locations = items[0]["source_reference"]["jira_code"]
    locations = locations if isinstance(locations, list) else [locations]
    assert any(location["cells"] == ["G2"] for location in locations)
    assert request.raw_data["cells"]["G2"] == "ETOJ-1; ETOJ-2\nETOJ-1"


def test_duplicate_item_rows_keep_all_source_codes(revision_storage):
    request = multiple_request(revision_storage)
    row = request.rows[0]
    sources = {**row.source_reference, "jira_code": {"sheet": "노트", "cells": ["G2"]}}
    first = replace(row, values={**row.values, "jira_code": "ETOJ-1"}, source_reference=sources)
    second = replace(row, row_ids=(max(row.row_ids) + 1,), values={**row.values, "jira_code": "ETOJ-2"},
                     source_reference=sources)
    DatabaseImportSaver(revision_storage).save(replace(request, rows=(first, second)))
    with revision_storage.transaction() as tx:
        items = EquipmentRepository().rows(tx, "item")
    assert len(items) == 1 and items[0]["jira_code"] == "ETOJ-1, ETOJ-2"


@pytest.mark.parametrize("complete_note", [False, True])
def test_explicit_clear_during_review_is_not_refilled_from_original(revision_storage, complete_note):
    request = multiple_request(revision_storage, confirmed=complete_note)
    row = request.rows[0]
    row = replace(row, values={**row.values, "jira_code": None},
                  edited_fields=tuple(dict.fromkeys((*row.edited_fields, "jira_code"))))
    request = replace(request, rows=(row,))
    saver = DatabaseImportSaver(revision_storage)
    if complete_note:
        request = reviewed_request(saver, request)
    saver.save(request)
    with revision_storage.transaction() as tx:
        items = EquipmentRepository().rows(tx, "item")
    assert len(items) == 1 and items[0]["jira_code"] is None
    assert "jira_code" in items[0]["edited_fields"]
    assert request.raw_data["cells"]["G2"] == "ETOJ-1; ETOJ-2\nETOJ-1"
