"""가져오기 화면의 시트 공통 정보(기준일·사업장·메모리/파운드리) 적용 검증 (1차 점검 6번)."""

import pytest
from PySide6.QtTest import QTest

from setron.features.ai.schemas import ExtractKind
from test_ui_import import app, excel, extracted, page, saver, window  # noqa: F401  (fixtures)


def _values(row):
    return {name: row.value(name) for name in ("base_date", "site", "production_area")}


def test_common_values_fill_only_empty_rows_and_overwrite_optionally(page):
    draft = extracted(page)
    assert page.kind is ExtractKind.EQUIPMENT_STATUS and page.common_box.isVisibleTo(page)
    before = {row.row_id: _values(row) for row in draft.rows if not row.excluded}
    page.common_date.setText("2026-10-05")
    page.common_site.setText("화성")
    assert page.apply_common()
    QTest.qWait(10)
    for row in page.draft.rows:
        if row.excluded:
            continue
        expected_date = before[row.row_id]["base_date"] or "2026-10-05"
        expected_site = before[row.row_id]["site"] or "화성"
        assert row.value("base_date") == expected_date and row.value("site") == expected_site
    # 덮어쓰기를 켜지 않으면 이미 값이 있는 칸은 유지된다.
    page.common_site.setText("평택")
    assert page.apply_common()
    for row in page.draft.rows:
        if not row.excluded and before[row.row_id]["site"]:
            assert row.value("site") == before[row.row_id]["site"]
    page.common_overwrite.setChecked(True)
    assert page.apply_common()
    assert all(row.value("site") == "평택" for row in page.draft.rows if not row.excluded)
    # 원문은 그대로 남고 수정값으로 기록된다.
    first = next(row for row in page.draft.rows if not row.excluded)
    assert first.edits.get("site") == "평택" and first.original.get("site") == draft.row(first.row_id).original.get("site")


def test_common_values_require_valid_date(page):
    extracted(page)
    page.common_date.setText("2026/13/40")
    assert not page.apply_common()
    assert "기준일 형식" in page.status.text()
    page.common_date.clear()
    page.common_site.clear()
    assert not page.apply_common()
    assert "공통 정보 없음" in page.status.text()


@pytest.mark.parametrize("digits", ["20260230", "20261301", "202611", "00000101"])
def test_common_date_mask_does_not_accept_invalid_dates_for_applying_rows(page, digits):
    original = extracted(page)
    page.common_date.clear()
    page.common_date.setFocus()
    QTest.keyClicks(page.common_date, digits)
    assert not page.apply_common()
    assert page.draft is original and "기준일 형식" in page.status.text()


def test_site_still_supports_selection_and_explicit_new_site_without_applying_it(page):
    original = extracted(page)
    page.configure_choices(sites=("화성", "평택"))
    combo = page.common_site_combo
    assert combo.isEditable()
    combo.setCurrentIndex(combo.findText("평택"))
    assert page.common_site.text() == "평택"
    page.common_site.selectAll()
    QTest.keyClicks(page.common_site, "New Site")
    assert page.common_site.text() == "New Site" and page.draft is original
