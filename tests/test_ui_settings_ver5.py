"""기준·설정 프론트/전체 테마. 운영 DB·AI·Excel에는 연결하지 않는다."""

from dataclasses import replace
import json
from threading import Event

import pytest
from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QDialogButtonBox, QGraphicsSimpleTextItem,
    QLabel, QLineEdit, QPushButton, QWidget,
)

from setron.ui.appearance import contrasting, manager, ui_color
from setron.ui.appearance_data import ACCENTS, Appearance, PersonalSettings
from setron.ui.components import label
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.query_state import LineOption, QuerySelection
from setron.ui.settings_location import AddReferenceDialog, LocationPanel
from setron.ui.settings_page import SettingsPage
from test_ui_import_ver5 import own, until  # noqa: F401


@pytest.fixture
def theme(own, tmp_path):
    current = manager()
    original, previous = current.store, current.appearance
    store = PersonalSettings(QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat))
    current.store = store
    current.apply(Appearance())
    yield current, store
    current.store = original
    current.apply(previous)


def page(own, theme):
    current, store = theme
    return own(SettingsPage(store=store, controller=current))


def test_appearance_validation_corrupt_restore_and_roundtrip(tmp_path):
    store = PersonalSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    assert store.appearance() == Appearance()
    with pytest.raises(ValueError):
        Appearance(accent="red")
    with pytest.raises(ValueError):
        Appearance(mode="invalid")
    with pytest.raises(ValueError):
        Appearance(font_size="huge")
    value = Appearance(mode="dark", accent="#12aacc", palette="custom", met="#155577", font_size="large")
    store.save("appearance", value)
    assert store.appearance() == value
    store.settings.setValue("appearance", "{broken")
    assert store.appearance() == Appearance()
    store.settings.setValue("appearance", json.dumps({"mode": "dark"}))
    assert store.appearance() == Appearance(mode="dark", font_size="normal")


def test_fixed_tabs_no_help_preview_cancel_reset_and_saved_theme(own, theme):
    current, store = theme
    p = page(own, theme)
    assert p.tabs.count() == 1 and p.tabs.tabText(0) == "화면 · 테마"
    assert not p.tabs.usesScrollButtons()
    captions = {widget.text() for widget in p.findChildren(QLabel)}
    assert not captions & {"위험 기준", "버전 계열 · 연결", "메뉴 순서 · 화면 배치", "나의 라인 · 설비군"}
    assert not any("도움말" in b.text() for b in p.findChildren(QPushButton))
    p.mode_buttons["dark"].click()
    assert p.draft.mode == "dark" and not current.dark
    assert "#242b36" in p.preview.styleSheet()
    p.cancel_button.click()
    assert p.draft.mode == "light" and not current.dark
    p.update_draft(mode="dark", accent="#12aacc")
    assert p.save_appearance()
    until(lambda: not p.operation.busy)
    assert current.dark and store.appearance().dark_colors["accent"] == "#12aacc"
    restored = page(own, theme)
    assert restored.draft.mode == "dark"
    p.reset_button.click()
    assert current.dark and p.draft == Appearance()
    p.cancel_button.click()
    assert p.draft.mode == "dark"


def test_invalid_hex_does_not_apply_or_persist(own, theme):
    p = page(own, theme)
    p.fields["accent"].setText("#wrong")
    assert not p.save_appearance()
    assert theme[1].appearance() == Appearance()
    assert not theme[0].dark


def test_location_scope_reset_preview_cancel_save_and_original_unchanged(own):
    source = demo_dashboard_source()
    loc = own(LocationPanel(source))
    original = dict(source.locations)
    assert loc.model.rowCount()
    loc.preview_mapping()
    assert "먼저 선택" in loc.feedback.text()
    loc._select_row(loc.model.index(0, 0))
    loc.preview_mapping()
    loc.confirm_dialog.reject()
    assert not loc.mapping_drafts
    loc.preview_mapping()
    loc.confirm_dialog.accept()
    assert loc.mapping_drafts and source.locations == original
    loc.site.setCurrentIndex(1)
    assert loc.selected is None and loc.fab.currentData() is None and loc.line.currentData() is None


def test_location_stale_confirm_blocked_and_addition_validation(own):
    loc = own(LocationPanel(demo_dashboard_source()))
    loc._select_row(loc.model.index(0, 0))
    loc.preview_mapping()
    loc._invalidate()
    loc.confirm_dialog.accept()
    assert not loc.mapping_drafts and "다시 확인" in loc.feedback.text()
    dialog = own(AddReferenceDialog("Site", ["평택"]))
    dialog.name.setText("평택")
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.name.setText("중국")
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_retired_personal_preferences_do_not_reorder_or_hide_choices(own, theme):
    _, store = theme
    source = demo_dashboard_source()
    line = LineOption("test-line", "테스트 Line", source.catalog.sites[0].key, None)
    source.catalog = replace(source.catalog, lines=(line,))
    group = source.catalog.groups[1]
    equipment_id = next(record.equipment_id for record in source.records
                        if source.locations[record.equipment_id].site_key == line.site_key
                        and any(pm.group_id in source.bindings[group.key] for pm in record.pms))
    source.locations[equipment_id] = replace(source.locations[equipment_id], line_key=line.key)
    store.save("personal", {"order": ["replay", "import", "ai", "items", "heatmap", "dashboard"],
                            "home": "heatmap", "lines": [], "groups": []})
    window = own(create_window(source))
    assert window.nav["settings"].caption.text() == "설정"
    assert window.query_state.catalog == source.catalog
    dashboard = window.pages["dashboard"]
    assert dashboard.layout().indexOf(dashboard.kpi_grid) < dashboard.layout().indexOf(dashboard.overview)
    window.query_panel.set_draft(QuerySelection(line_key=line.key, group_key=group.key))
    query = window.query_panel.apply_draft()
    snapshot = window.pages["dashboard"].snapshot
    assert snapshot.equipment_ids == (equipment_id,)
    assert window.pages["heatmap"].snapshot is snapshot
    assert window.pages["equipment"].snapshot is snapshot
    window.navigate("settings")
    assert window.header.title.text() == "설정"
    settings = window.pages["settings"]
    settings.font_size_buttons["small"].click()
    assert settings.save_appearance()
    until(lambda: not settings.operation.busy)
    assert window.query_state.applied is query and window.pages["dashboard"].snapshot is snapshot


def test_whole_app_dark_existing_new_widgets_charts_and_light_restore(own, theme):
    current, store = theme
    w = own(create_window())
    query = w.query_state.draft
    assert "settings" in w.pages and w.nav["settings"].isEnabled()
    dashboard = w.pages["dashboard"]
    assert dashboard.content.parentWidget() is dashboard.overview
    current.apply(Appearance(mode="dark"))
    for key in w.pages:
        w.navigate(key)
        QApplication.processEvents()
        assert w.palette().color(QPalette.ColorRole.Window).lightnessF() < .2
    assert w.query_state.draft == query
    dialog = own(QDialog(w))
    text = QLabel("팝업 글자", dialog)
    text.setStyleSheet("color:#142033;background:white;")
    QApplication.processEvents()
    assert "#e8eef9" in text.styleSheet() and "#242b36" in text.styleSheet()
    assert ui_color("#142033").lightnessF() > .8
    chart = w.pages["dashboard"].chart.chart()
    assert chart.backgroundBrush().color().lightnessF() < .3
    current.apply(Appearance())
    assert "#142033" in text.styleSheet() and "#ffffff" in text.styleSheet()
    assert ui_color("#142033") == QColor("#142033")
    assert chart.backgroundBrush().color() == QColor("white")


def test_system_mode_and_distinct_custom_palette(own, theme):
    current, store = theme
    current.apply(Appearance(mode="system"))
    assert current.dark == (QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    p = page(own, theme)
    p.update_draft(palette="distinct")
    assert "#d8f1fa" in p.preview_tiles[0][0].styleSheet() if not current.dark else True
    p.fields["met"].setText("#112233")
    p._edit_color("met")
    assert p.draft.palette == "custom" and p.draft.met == "#112233"


def test_light_background_does_not_leak_into_dark_and_mode_colors_are_independent(own, theme):
    current, store = theme
    legacy = Appearance(canvas="#F5F6F8", surface="#FFFFFF", accent="#3c5ce9", rate="#7C4AE5")
    store.save("appearance", legacy)
    current.apply(legacy)
    p = page(own, theme)
    p.update_draft(mode="dark")
    assert p.draft.colors(True)["surface"] == "#242b36"
    assert p.draft.colors(True)["canvas"] == "#11151c"
    assert p.draft.colors(True)["accent"] == "#8ea2ff"
    p.fields["surface"].setText("#283040")
    p._edit_color("surface")
    assert p.draft.dark_colors["surface"] == "#283040"
    p.update_draft(mode="light")
    assert p.fields["surface"].text() == "#FFFFFF"
    p.update_draft(mode="dark")
    assert p.fields["surface"].text() == "#283040"


def test_existing_light_tones_and_primary_text_contrast(own, theme):
    current, _ = theme
    from setron.ui.components import button
    for value in ("#d4ebdf", "#fbe5a3", "#fce2e7", "#3d5afe"):
        assert current.color(value, "background") == value
    action = own(button("조회 적용", primary=True))
    assert action.palette().color(QPalette.ColorRole.ButtonText).lightnessF() > .9
    assert contrasting("#3c5ce9") == "#f4f7ff"
    current.apply(Appearance(mode="dark"))
    assert action.palette().color(QPalette.ColorRole.ButtonText).lightnessF() < .3
    assert "#a8baff" in QApplication.instance().styleSheet()


def test_actual_dark_pixels_existing_light_hex_and_light_restore(own, theme):
    current, store = theme
    w = own(create_window())
    w.navigate("settings")
    current.apply(Appearance(mode="dark", canvas="#F5F6F8", surface="#FFFFFF"))
    QApplication.processEvents()
    # QPalette만 검사하면 흰 배경 + 밝은 글자의 실제 렌더링 결함을 놓친다.
    canvas = w.centralWidget().grab().toImage()
    # 우측 끝은 스크롤바이므로 본문 위쪽의 실제 여백을 표본으로 잡는다.
    assert canvas.pixelColor(300, 10).name() == "#11151c"
    sidebar = w.findChild(QWidget, "sidebar")
    assert sidebar.grab().toImage().pixelColor(4, 4).name() == "#242b36"
    p = w.pages["settings"]
    assert p.font_size_buttons["normal"].palette().color(QPalette.ColorRole.ButtonText).lightnessF() > .5
    current.apply(Appearance())
    QApplication.processEvents()
    assert w.centralWidget().grab().toImage().pixelColor(300, 10).name() == "#f5f6f8"
    assert w.nav["dashboard"].caption.palette().color(QPalette.ColorRole.WindowText).lightnessF() < .3


def test_background_settings_save_popup_animation_duplicate_and_failure(own, theme, monkeypatch):
    p = page(own, theme)
    gate = Event()
    original_writer = p.store.writer
    def writer(key, value):
        actual = original_writer(key, value)
        def write():
            gate.wait(4)
            actual()
        return write
    monkeypatch.setattr(p.store, "writer", writer)
    p.update_draft(mode="dark")
    try:
        assert p.save_appearance()
        assert p.operation.dialog.isVisible()
        assert "저장 중" in p.operation.dialog.heading.text()
        assert not p.save_appearance()
        QTest.qWait(80)
        phase = p.operation.dialog.progress.phase
        QTest.qWait(100)
        assert p.operation.dialog.progress.phase != phase
        assert not theme[0].dark
    finally:
        gate.set()
    until(lambda: not p.operation.busy)
    assert theme[0].dark and not p.operation.dialog.isVisible()
    previous = theme[0].appearance
    def fail():
        raise OSError("failure")
    monkeypatch.setattr(p.store, "writer", lambda *_: fail)
    p.update_draft(mode="light")
    assert p.save_appearance()
    until(lambda: not p.operation.busy)
    assert theme[0].appearance == previous
    assert "저장하지 못했습니다" in p.feedback.text()


def test_font_size_preview_save_reload_and_reset(own, theme):
    current, store = theme
    p = page(own, theme)
    def choice_sizes(settings):
        return [settings.font_size_buttons[key].font().pixelSize() for key in ("small", "normal", "large")]

    assert p.font_size_buttons["normal"].isChecked()
    assert choice_sizes(p) == [12, 14, 16]
    p.font_size_buttons["large"].click()
    assert p.draft.font_size == "large"
    assert p.preview_value.font().pixelSize() > 46
    assert current.appearance.font_size == "normal"
    p.cancel_button.click()
    assert p.preview_value.font().pixelSize() == 46
    p.font_size_buttons["small"].click()
    p.save_button.click()
    until(lambda: not p.operation.busy)
    assert current.appearance.font_size == store.appearance().font_size == "small"
    assert QApplication.instance().font().pixelSize() == 12
    assert p.font_size_buttons["small"].isChecked()
    assert choice_sizes(p) == [12, 14, 16]
    restored = page(own, theme)
    assert restored.font_size_buttons["small"].isChecked()
    assert choice_sizes(restored) == [12, 14, 16]
    p.reset_button.click()
    assert current.appearance.font_size == "small" and p.draft.font_size == "normal"
    p.save_button.click()
    until(lambda: not p.operation.busy)
    assert current.appearance.font_size == store.appearance().font_size == "normal"


def test_global_fonts_cover_existing_and_new_widgets_and_charts(own, theme):
    current, _ = theme
    w = own(create_window())
    w.query_panel.apply_draft()
    dashboard = w.pages["dashboard"]
    dashboard.chart.composition(dashboard.snapshot)
    chart = dashboard.chart.chart()
    donut = next(item for item in chart.scene().items()
                 if isinstance(item, QGraphicsSimpleTextItem) and item.font().pixelSize() == 34)
    popup = own(QDialog(w))
    inherited = QLineEdit("날짜", popup)
    styled = QLabel("표 제목", popup)
    styled.setStyleSheet("font-size:15px;")
    existing = w.nav["dashboard"].caption
    baseline = (existing.font().pixelSize(), inherited.font().pixelSize(),
                donut.font().pixelSize(), w.header.title.font().pixelSize())
    for size, direction in (("large", 1), ("small", -1), ("normal", 0)):
        current.apply(Appearance(mode="dark" if size != "normal" else "light", font_size=size))
        QApplication.processEvents()
        actual = (existing.font().pixelSize(), inherited.font().pixelSize(),
                  donut.font().pixelSize(), w.header.title.font().pixelSize())
        assert all((x > y if direction == 1 else x < y if direction == -1 else x == y)
                   for x, y in zip(actual, baseline))
        assert styled.font().pixelSize() == round(15 * current.appearance.font_scale)
        fresh = label("새 팝업 글자", 14)
        assert fresh.font().pixelSize() == round(14 * current.appearance.font_scale)
        fresh.deleteLater()
    assert w.query_state.catalog == dashboard.source.catalog


def test_display_choices_highlight_and_reopen_at_applied_values(own, theme):
    current, store = theme
    current.apply(Appearance(mode="dark", font_size="large"))
    assert store.appearance().font_size == "normal"
    window = own(create_window())
    window.navigate("settings")
    p = window.pages["settings"]
    small, normal, large = (p.font_size_buttons[key] for key in ("small", "normal", "large"))
    assert large.isChecked() and not normal.isChecked()
    assert p.mode_buttons["dark"].isChecked()
    assert p.accent_buttons["Blue"].isChecked()
    assert p.palette_buttons["standard"].isChecked()
    original_canvas = p.fields["canvas"].text()

    small.click()
    p.mode_buttons["light"].click()
    p.accent_buttons["Teal"].click()
    p.palette_buttons["distinct"].click()
    p.fields["canvas"].setText("#ABCDEF")
    p._edit_color("canvas")
    assert small.isChecked() and not large.isChecked()
    assert p.accent_buttons["Teal"].isChecked()
    assert p.palette_buttons["distinct"].isChecked()
    assert current.appearance.font_size == "large"
    assert p.mode_buttons["light"].isChecked() and current.dark
    window.navigate("dashboard")
    window.navigate("settings")
    assert large.isChecked() and not small.isChecked()
    assert p.mode_buttons["dark"].isChecked()
    assert not p.mode_buttons["light"].isChecked()
    assert p.accent_buttons["Blue"].isChecked()
    assert p.palette_buttons["standard"].isChecked()
    assert p.fields["canvas"].text() == original_canvas

    current.apply(Appearance(mode="system", font_size="small", palette="distinct",
                             accent=ACCENTS["Violet"], dark_colors={"accent": ACCENTS["Violet"]}))
    window.navigate("dashboard")
    window.navigate("settings")
    assert small.isChecked() and not large.isChecked()
    assert p.mode_buttons["system"].isChecked()
    assert p.accent_buttons["Violet"].isChecked()
    assert p.palette_buttons["distinct"].isChecked()


def test_accent_presets_align_with_custom_color_field(own, theme):
    current, _ = theme
    p = page(own, theme)
    p.resize(1460, 1150)
    field = p.fields["accent"]
    for size in ("normal", "large", "small"):
        current.apply(Appearance(font_size=size))
        QApplication.processEvents()
        field_y = field.mapTo(p, QPoint()).y()
        for b in p.accent_buttons.values():
            assert b.mapTo(p, QPoint()).y() == field_y
            assert b.height() == field.height()
        picker = p.color_picker_buttons["accent"]
        assert picker.mapTo(p, QPoint()).y() == field_y
        assert picker.height() == field.height()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_color_palettes_use_current_color_preview_and_save(own, theme, mode):
    current, store = theme
    applied = Appearance(mode=mode)
    current.apply(applied)
    p = page(own, theme)
    values = {"met": "#25823f", "below": "#d7b54e", "risk": "#d46165",
              "rate": "#5c71c9", "accent": "#167e92", "canvas": "#304759", "surface": "#4b5864"}
    assert set(p.color_picker_buttons) == set(values)

    def open_palette(key):
        p.color_picker_buttons[key].click()
        dialog = next(d for d in p.findChildren(QColorDialog) if d.isVisible())
        assert dialog.currentColor().name().upper() == p.fields[key].text()
        return dialog

    dialog = open_palette("accent")
    dialog.setCurrentColor(QColor("#AB12CD"))
    dialog.reject()
    assert p.draft == applied
    for key, value in values.items():
        dialog = open_palette(key)
        dialog.setCurrentColor(QColor(value))
        confirm = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        assert confirm.text() == "확인"
        confirm.click()
        assert p.fields[key].text() == value.upper()
        swatch = p.color_picker_buttons[key]
        assert swatch.icon().pixmap(swatch.iconSize()).toImage().pixelColor(9, 9) == QColor(value)

    assert p.draft.palette == "custom"
    assert current.appearance == applied
    assert store.appearance() == Appearance()
    if mode == "dark":
        assert p.draft.dark_colors == values and p.draft.canvas is None
    else:
        assert p.draft.dark_colors == {}
    assert p.save_appearance()
    until(lambda: not p.operation.busy)
    assert store.appearance() == current.appearance == p.draft


def test_clicking_settings_options_keeps_control_fonts_and_heights(own, theme):
    current, _ = theme
    current.apply(Appearance(mode="dark", font_size="large"))
    p = page(own, theme)
    p.resize(1460, 1150)
    current.apply(Appearance(mode="dark", font_size="normal"))
    groups = (p.palette_buttons, p.accent_buttons, p.mode_buttons, p.font_size_buttons)
    buttons = [b for group in groups for b in group.values()]
    controls = [*buttons, *p.fields.values(), p.reset_button, p.cancel_button, p.save_button]
    QApplication.processEvents()
    baseline = [(w.font(), w.height()) for w in controls]
    for b in [*buttons, p.reset_button, p.cancel_button]:
        b.setFocus()
        QTest.mouseClick(b, Qt.MouseButton.LeftButton)
        QApplication.processEvents()
        actual = [(w.font(), w.height()) for w in controls]
        assert actual == baseline, (b.accessibleName() or b.text(),
            [(w.accessibleName(), before[0].pixelSize(), after[0].pixelSize(), before[1], after[1])
             for w, before, after in zip(controls, baseline, actual) if before != after])


def test_accent_palette_checked_buttons_render_blue_in_dark_mode(own, theme):
    current, _ = theme
    current.apply(Appearance(mode="dark"))
    p = page(own, theme)
    def background(control):
        return control.grab().toImage().pixelColor(18, 10).name()

    basic, distinct = (p.palette_buttons[key] for key in ("standard", "distinct"))
    selected, unselected = background(basic), background(distinct)
    assert selected == current.tokens["selected"]
    assert unselected != selected
    distinct.click()
    assert background(distinct) == selected
    assert background(basic) == unselected
    assert background(p.accent_buttons["Blue"]) == selected
    p.update_draft(accent="#124567")
    assert not any(b.isChecked() for b in p.accent_buttons.values())
    assert p.custom_accent_label.palette().color(QPalette.ColorRole.WindowText).name() == current.tokens["accent"]


def test_canvas_live_preview_is_not_saved_and_clears_on_cancel_or_leave(own, theme):
    current, store = theme
    window = own(create_window())
    window.navigate("settings")
    p = window.pages["settings"]
    canvas = window.scroll.widget()
    baseline = current.tokens["canvas"]

    def background():
        return canvas.grab(QRect(4, 4, 1, 1)).toImage().pixelColor(0, 0).name()

    p.color_picker_buttons["canvas"].click()
    picker = next(d for d in p.findChildren(QColorDialog) if d.isVisible())
    picker.setCurrentColor(QColor("#ecd2b8"))
    assert background() == "#ecd2b8"
    assert p.draft.canvas is None
    picker.reject()
    assert background() == baseline

    field = p.fields["canvas"]
    field.setText("#C8DDED")
    field.textEdited.emit(field.text())
    assert background() == "#c8dded"
    p._edit_color("canvas")
    assert current.tokens["canvas"] == baseline and store.appearance().canvas is None
    p.cancel_button.click()
    assert background() == baseline

    p.update_draft(canvas="#e9e1d4")
    assert background() == "#e9e1d4"
    window.navigate("dashboard")
    assert background() == baseline
    window.navigate("settings")
    assert p.fields["canvas"].text() == baseline.upper()


def test_settings_preview_scroll_follows_only_in_wide_layout(own, theme):
    window = own(create_window())
    window.resize(1660, 700)
    window.navigate("settings")
    p = window.pages["settings"]
    scroll = window.scroll.verticalScrollBar()
    viewport = window.scroll.viewport()
    QApplication.processEvents()
    assert p.appearance_row.width() >= p.preview_column.breakpoint
    before = p.preview.mapTo(viewport, QPoint()).y()
    maximum = scroll.maximum()
    assert maximum > before
    scroll.setValue(min(maximum, before + 50))
    QApplication.processEvents()
    assert p.preview.y() > 0
    assert 0 <= p.preview.mapTo(viewport, QPoint()).y() <= before
    assert scroll.maximum() == maximum

    window.resize(960, 700)
    QApplication.processEvents()
    scroll.setValue(scroll.maximum())
    QApplication.processEvents()
    assert p.preview.y() == 0
    assert p.preview_column.y() >= p.appearance_row.left.geometry().bottom()


def test_management_save_progress_success_failure_unknown_and_no_duplicate(own, theme):
    w = own(create_window())
    management, versions = w.pages["management"], w.pages["versions"]
    progress = w.management_progress
    management.save_started.emit()
    assert progress.isVisible() and "저장 중" in progress.heading.text()
    management.save_failed.emit()
    assert not progress.isVisible()
    management.save_started.emit()
    management.save_uncertain.emit()
    assert not progress.isVisible()
    versions.progress.set_operation("save", "저장 중", "설치 버전 저장 중")
    versions.save_started.emit()
    assert versions.progress.isVisible() and not progress.isVisible()
    versions.progress.set_operation(None)
    versions.save_uncertain.emit()
    assert not w.management_saving
