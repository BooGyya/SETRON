"""선택한 내용/보기만 고정된 Word·PDF에 포함되는지 검증한다."""

from dataclasses import replace
from zipfile import ZipFile

import pytest
from docx import Document
from PySide6.QtCore import QDate
from PySide6.QtTest import QTest
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QCheckBox

from setron.features.chat.schemas import ChatAnswer, ChatRequest, Intent
from setron.ui.heatmap_grouping import GROUPING_MODES
from setron.ui.report_data import COMMON_CONTENTS, REPORT_TYPES, generate_report
from setron.ui.report_export import pdf_document, report_html, save_generated_report
from setron.ui.report_widgets import report_pictures
from setron.ui.version_demo import demo_version_catalog
from test_report_mentoring6 import DAYS, qapp, report_source, request  # noqa: F401
from test_ui_import_ver5 import own, until  # noqa: F401
from test_ui_reports_ver5 import generated, page


@pytest.mark.parametrize("key", [key for key, *_ in COMMON_CONTENTS])
def test_each_common_content_can_be_exported_alone(key):
    source = report_source()
    result = generate_report(source, request(source, contents=(key,), types=()))
    assert result.sections and {s.content_key for s in result.sections} == {key}
    assert result.title == "맞춤 보고서"


@pytest.mark.parametrize("key", [key for key, *_ in REPORT_TYPES])
def test_each_type_can_be_exported_without_other_common_or_type_contents(own, tmp_path, key):
    p = page(own, tmp_path)
    catalog = demo_version_catalog(p.source)
    answer = ChatAnswer(ChatRequest("검증 질문", (Intent.SEARCH,)), "검증 범위", "선택한 AI 요약")
    from test_ui_reports_ver5 import request as demo_request
    result = generate_report(p.source, demo_request(p.source, contents=(), types=(key,),
        item_id=catalog.items[0].item.item_id, ai_answer=answer), catalog)
    assert result.sections and {s.content_key for s in result.sections} == {key}
    assert "목표 N 달성률" not in report_html(result)


def test_multiple_types_are_additive_and_unselected_item_does_not_require_input(own, tmp_path):
    p = page(own, tmp_path)
    p.select_contents(False)
    for key in ("upgrade", "change"):
        p.type_buttons[key].click()
    result = generated(p)
    assert result.request.selected_types == ("upgrade", "change")
    assert {s.content_key for s in result.sections} == {"upgrade", "change"}
    assert result.title == "맞춤 보고서" and p.item.isVisibleTo(p) and not p.item.isEnabled()


def test_select_all_clear_and_item_dependency_are_explicit(own, tmp_path):
    answer = ChatAnswer(ChatRequest("검증 질문", (Intent.SEARCH,)), "검증 범위", "선택한 AI 요약")
    p = page(own, tmp_path, source=report_source(), ai_provider=lambda: answer)
    assert all(isinstance(c, QCheckBox) for c in p.type_buttons.values())
    saved = list(p.saved_entries)
    assert p.select_all_button.text() == "전체 선택"
    p.select_all_button.click()
    until(lambda: not p.item_request.busy)
    assert all(c.isChecked() for c in (*p.content_checks.values(), *p.type_buttons.values(), *p.heatmap_checks.values()))
    assert p.select_all_button.text() == "전체 해제"
    assert not p.generate() and "Item" in p.alert_dialog.message.text()
    assert p.generation_status.isHidden()
    p.alert_dialog.accept()
    p.item.setCurrentIndex(1)
    result = generated(p)
    assert set(result.request.selected_types) == {key for key, *_ in REPORT_TYPES}
    assert result.request.heatmap_modes == tuple(key for key, _ in GROUPING_MODES)
    p.content_checks["summary"].click()
    assert p.select_all_button.text() == "전체 선택"
    p.select_all_button.click()
    assert p.select_all_button.text() == "전체 해제"
    p.select_all_button.click()
    assert not any(c.isChecked() for c in (*p.content_checks.values(), *p.type_buttons.values(), *p.heatmap_checks.values()))
    assert p.select_all_button.text() == "전체 선택"
    assert not p.generate() and "하나 이상" in p.alert_dialog.message.text()
    p.alert_dialog.accept()
    assert p.result is result and p.saved_entries == saved
    p.content_checks["summary"].click()
    assert {s.content_key for s in generated(p).sections} == {"summary"}


def test_heatmap_views_directly_control_inclusion_without_an_extra_checkbox(own, tmp_path):
    p = page(own, tmp_path)
    p.select_contents(False)
    assert "heatmap" not in p.content_checks
    assert p.heatmap_options.isEnabled()
    assert not p.generate() and "하나 이상" in p.alert_dialog.message.text()
    p.alert_dialog.accept()
    p.heatmap_checks["fab"].click()
    result = generated(p)
    assert [s.title for s in result.sections] == ["분류별 히트맵"]
    assert result.request.contents == ("heatmap",) and result.request.heatmap_modes == ("fab",)
    p.heatmap_checks["fab"].click()
    assert p.heatmap_options.isEnabled()
    p.type_buttons["status"].click()
    assert not any(s.chart == "heatmap" for s in generated(p).sections)


def test_all_heatmap_views_keep_original_lines_and_dfb_and_subline_excludes_invented_values():
    source = report_source()
    result = generate_report(source, request(source, contents=("heatmap",), types=(),
        heatmap_modes=tuple(key for key, _ in GROUPING_MODES)))
    assert [s.title for s in result.sections] == ["사업장별 히트맵", "분류별 히트맵", "라인별 히트맵",
                                                "세부라인별 히트맵", "설비군·라인별 히트맵"]
    for section in result.sections:
        cells = [cell for panel in section.visual for _, entries in panel.lines for cell in entries]
        assert cells and all(cell.code != "EQ-6" for cell in cells)  # HPA
        if section.title == "세부라인별 히트맵":
            assert {cell.code for cell in cells} == {"EQ-1", "EQ-2", "EQ-3"}
            assert {panel.group for panel in section.visual} == {"P1F", "P1D", "P4H"}
        else:
            assert {cell.code for cell in cells} == {"EQ-1", "EQ-2", "EQ-3", "EQ-4", "EQ-5", "EQ-7"}
            text = " ".join(panel.group + " ".join(line for line, _ in panel.lines) for panel in section.visual)
            assert "DFB" in text and "P3-S5" in text


def test_selected_heatmap_views_obey_memory_foundry_scope():
    from setron.ui.query_state import QuerySelection
    source = report_source()
    for area in ("MEMORY", "FOUNDRY"):
        result = generate_report(source, request(source, selection=QuerySelection(fab_key=area), contents=("heatmap",), types=(),
            heatmap_modes=("site", "fab", "line", "subline")))
        expected = {"EQ-1", "EQ-2", "EQ-3", "EQ-7"} if area == "MEMORY" else {"EQ-4", "EQ-5"}
        for section in result.sections:
            cells = {cell.code for panel in section.visual for _, entries in panel.lines for cell in entries}
            assert cells <= expected
            if section.title != "세부라인별 히트맵":
                assert cells == expected


def test_request_freezes_choices_and_legacy_flags_are_respected():
    source = report_source()
    contents, types, modes = ["heatmap"], [], ["fab"]
    selected = request(source, contents=contents, types=types, heatmap_modes=modes)
    contents.append("summary")
    types.append("item")
    modes.append("site")
    assert selected.contents == ("heatmap",) and selected.types == () and selected.heatmap_modes == ("fab",)
    result = generate_report(source, request(source, trend=False, heatmap=False))
    assert not any(s.chart in {"heatmap", "line_trend", "trend"} for s in result.sections)


@pytest.mark.parametrize("changes", [{"contents": (), "types": ()}, {"contents": ("invalid",)},
    {"types": ("invalid",)}, {"heatmap_modes": ("invalid",)}, {"contents": ("heatmap",), "heatmap_modes": ()}])
def test_invalid_content_or_view_is_rejected(changes):
    with pytest.raises(ValueError):
        request(report_source(), **changes)


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_exported_files_contain_only_selected_sections_and_view_images(qapp, tmp_path, fmt):
    source = report_source()
    result = generate_report(source, request(source, format=fmt, contents=("heatmap",), types=("change",),
        heatmap_modes=("fab", "subline")))
    pictures = report_pictures(result)
    assert pictures and all(result.sections[index].chart == "heatmap" for index, _ in pictures)
    path = tmp_path / result.filename
    assert save_generated_report(result, path, pictures).saved
    if fmt == "docx":
        doc = Document(path)
        text = " ".join(p.text for p in doc.paragraphs)
        with ZipFile(path) as archive:
            assert len([n for n in archive.namelist() if n.startswith("word/media/")]) == len(pictures)
    else:
        doc = QPdfDocument()
        assert doc.load(str(path)) == QPdfDocument.Error.None_
        text = " ".join(doc.getAllText(i).text() for i in range(doc.pageCount()))
        doc.close()
        assert path.read_bytes().count(b"/Subtype /Image") == len(pictures)
    for expected in ("분류별 히트맵", "세부라인별 히트맵", "설비별 변경 이력"):
        assert expected in text
    for excluded in ("목표 N 달성률", "날짜별 적용률 추이", "위험 후보 설비와 근거", "설비 목록·원문 근거",
                     "사업장별 히트맵", "설비군·라인별 히트맵", "업그레이드 필요 설비"):
        assert excluded not in text


def test_editing_checkboxes_does_not_change_generated_selection_until_generate(own, tmp_path):
    p = page(own, tmp_path)
    first = generated(p)
    html = report_html(first)
    p.select_contents(False)
    p.content_checks["summary"].click()
    assert p.result is first and report_html(first) == html and not p.request.busy
    second = generated(p)
    assert second.request.selected_contents == ("summary",) and second.request.selected_types == ()
    assert report_html(first) == html


def test_ai_navigation_selects_requested_type_without_keeping_previous_types(own, tmp_path):
    p = page(own, tmp_path)
    p.type_buttons["upgrade"].click()
    assert p.open_period("change", DAYS[0], DAYS[-1])
    assert p.selected_types == ("change",) and p.item.isVisibleTo(p) and not p.item.isEnabled()
    assert p.start.date() == QDate(*DAYS[0].timetuple()[:3])


def test_checkboxes_support_keyboard_and_remain_readable_in_dark_custom_surfaces(own, tmp_path):
    import re
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication
    from setron.ui.appearance import contrast_ratio, install_appearance
    from setron.ui.appearance_data import Appearance, PersonalSettings

    p = page(own, tmp_path)
    controller = install_appearance(QApplication.instance(), PersonalSettings(
        QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)))
    previous = controller.appearance
    from test_ui_reports_ver5 import request as demo_request
    report = generate_report(p.source, demo_request(p.source, contents=("summary",), types=()))
    try:
        for appearance in (Appearance(mode="dark", font_size="large"),
                           Appearance(surface="#112255"), Appearance(surface="#fff700")):
            controller.apply(appearance)
            QTest.qWait(20)
            controls = (*p.content_checks.values(), *p.type_buttons.values(), *p.heatmap_checks.values())
            sizes = [c.font().pixelSize() for c in controls]
            for control in controls:
                foreground = re.search(r"QCheckBox \{color:([^;]+);", control.styleSheet()).group(1)
                assert contrast_ratio(foreground, controller.tokens["surface"]) >= 4.5
            check = p.type_buttons["upgrade"]
            before = check.isChecked()
            check.setFocus()
            QTest.keyClick(check, Qt.Key.Key_Space)
            assert check.isChecked() != before
            assert p.type_buttons["status"].isChecked()
            assert sizes == [c.font().pixelSize() for c in controls]
            document = pdf_document(report, ())
            for text in ("조회 범위의 분모", "물리 설비(대)"):
                cursor = document.find(text)
                assert not cursor.isNull()
                assert cursor.charFormat().foreground().color().name() == "#142033"
    finally:
        controller.apply(previous)
