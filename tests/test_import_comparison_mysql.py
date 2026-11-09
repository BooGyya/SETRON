"""실제 저장 형식에서 가져오기 비교 범위와 호기 단위 집계를 확인한다."""

import os
from dataclasses import replace
from datetime import date
from time import monotonic, sleep

import pytest

from PySide6.QtWidgets import QApplication

from setron.features.ai.schemas import ExtractKind
from setron.features.import_save.service import DatabaseImportSaver
from setron.ui.import_page import ImportPage
from setron.features.excel_import.comparison import ComparisonScopeError, compare_import
from test_import_save_mysql import EQUIPMENT, request, row, storage
from test_import_validation import draft, row as review_row
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="전용 MySQL 설정 필요")


def test_compare_saved_site_sheet_and_physical_equipment(storage):
    def save(day, site, sheet, codes):
        values = dict(EQUIPMENT, base_date=day, site=site, model_name="M-O2", group_name="Oxide",
                      family_name="O2", pm_name="PM1", version_raw="v1")
        rows = [row(i, {"equipment_code": f"A{i}", "version_raw": f"B{i}"}, equipment_code=code, **values)
                for i, code in enumerate(codes, 1)]
        req = request(ExtractKind.EQUIPMENT_STATUS, rows, f"{day}-{site}-{sheet}")
        prepared = tuple(replace(r, source_reference={k: dict(v, sheet=sheet) for k, v in r.source_reference.items()})
                         for r in req.rows)
        DatabaseImportSaver(storage).save(replace(req, rows=prepared, raw_data=dict(req.raw_data, sheet=sheet)))

    save("2026-10-21", "화성", "Memory", ("A", "B", "C"))
    save("2026-10-23", "화성", "Foundry", ("D",))
    save("2026-10-24", "평택", "Memory", ("E",))
    save("2026-11-02", "화성", "Memory", ("당일자료",))
    save("2026-11-03", "화성", "Memory", ("미래자료",))
    current = draft(review_row(1, equipment_code="A"), review_row(2, equipment_code="B", version_raw="v2"),
                    review_row(3, equipment_code="B", pm_name="PM2"), review_row(4, equipment_code="F"),
                    replace(review_row(5, equipment_code="제외"), excluded=True), sheet="Memory")
    reference = DatabaseImportSaver(storage).load_reference((current,))
    app = QApplication.instance() or QApplication([])
    page = ImportPage(saver=DatabaseImportSaver(storage), reference=reference)
    page.draft = current
    page._changed(initial=True)
    deadline = monotonic() + 15
    while page.busy and monotonic() < deadline:
        app.processEvents()
        sleep(0.01)
    result = page.comparison_card.comparison
    assert not page.busy
    page.close()
    assert result is not None, [(issue.code, issue.message) for issue in page.issues]
    assert result.previous_date == date(2026, 10, 21)
    assert (result.changed, result.added, result.unchanged, result.absent) == (1, 1, 1, 1)
    assert compare_import(storage, replace(current, sheet=replace(current.sheet, sheet_name="없는 시트")), reference) is None
    other_site = review_row(6, site="평택", equipment_code="E")
    with pytest.raises(ComparisonScopeError, match="사업장"):
        compare_import(storage, replace(current, rows=(*current.rows, other_site)), reference)
