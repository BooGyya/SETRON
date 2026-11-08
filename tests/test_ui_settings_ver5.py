"""기준·설정 프론트/전체 테마. 운영 DB·AI·Excel에는 연결하지 않는다."""

from dataclasses import replace
import json

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from setron.ui.appearance import manager, ui_color
from setron.ui.appearance_data import Appearance, MENU_ORDER, PersonalSettings
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.settings_location import AddReferenceDialog
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
    return own(SettingsPage(demo_dashboard_source(), store=store, controller=current))


def test_appearance_validation_corrupt_restore_and_roundtrip(tmp_path):
    store = PersonalSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    assert store.appearance() == Appearance()
    with pytest.raises(ValueError):
        Appearance(accent="red")
    with pytest.raises(ValueError):
        Appearance(mode="invalid")
    value = Appearance(mode="dark", accent="#12aacc", palette="custom", met="#155577")
    store.save("appearance", value)
    assert store.appearance() == value
    store.settings.setValue("appearance", "{broken")
    assert store.appearance() == Appearance()
    store.settings.setValue("personal", json.dumps({"order": ["dashboard"]}))
    assert store.personal()["order"] == MENU_ORDER


def test_fixed_tabs_no_help_preview_cancel_reset_and_saved_theme(own, theme):
    current, store = theme
    p = page(own, theme)
    assert p.tabs.count() == 3 and not p.tabs.usesScrollButtons()
    assert p.stack.currentWidget() is p.appearance_page
    assert not any("도움말" in b.text() for b in p.findChildren(QPushButton))
    p.mode_buttons["dark"].click()
    assert p.draft.mode == "dark" and not current.dark
    assert "#242b36" in p.preview.styleSheet()
    p.cancel_button.click()
    assert p.draft.mode == "light" and not current.dark
    p.update_draft(mode="dark", accent="#12aacc")
    assert p.save_appearance()
    assert current.dark and store.appearance().accent == "#12aacc"
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


def test_menu_order_draft_preview_cancel_save_and_defaults(own, theme):
    p = page(own, theme)
    emitted = []
    p.personal_changed.connect(emitted.append)
    p.open_layout()
    assert not p.menu_up["dashboard"].isEnabled()
    p.move_menu(0, 1)
    assert p.order_draft[0] == "heatmap" and not emitted
    assert p.menu_preview.text().index("버전 히트맵") < p.menu_preview.text().index("대시보드")
    p.cancel_personal()
    assert p.personal["order"] == MENU_ORDER
    p.open_layout()
    p.move_menu(0, 1)
    p._set_home("heatmap")
    assert p.save_personal()
    assert emitted[-1]["order"][0] == "heatmap" and emitted[-1]["home"] == "heatmap"
    assert p.store.personal()["home"] == "heatmap"
    p.open_layout()
    p._reset_personal()
    assert p.order_draft == list(MENU_ORDER)


def test_personal_line_group_selection_is_local_and_explicit(own, theme):
    p = page(own, theme)
    p.open_personal()
    dialog = p.personal_dialog
    first = next(k for k in dialog.checks if k[0] == "groups")
    dialog.checks[first].setChecked(False)
    dialog.accept()
    QApplication.processEvents()
    assert first[1] not in p.personal["groups"]
    assert p.source.catalog.groups


def test_location_scope_reset_preview_cancel_save_and_original_unchanged(own, theme):
    p = page(own, theme)
    loc = p.location
    original = dict(p.source.locations)
    p.tabs.setCurrentIndex(1)
    p.stack.setCurrentWidget(loc)
    assert loc.model.rowCount()
    loc.preview_mapping()
    assert "먼저 선택" in loc.feedback.text()
    loc._select_row(loc.model.index(0, 0))
    loc.preview_mapping()
    loc.confirm_dialog.reject()
    assert not loc.mapping_drafts
    loc.preview_mapping()
    loc.confirm_dialog.accept()
    assert loc.mapping_drafts and p.source.locations == original
    loc.site.setCurrentIndex(1)
    assert loc.selected is None and loc.fab.currentData() is None and loc.line.currentData() is None
    loc.back_requested.emit()
    assert p.stack.currentWidget() is p.family_page


def test_location_stale_confirm_blocked_and_addition_validation(own, theme):
    p = page(own, theme)
    loc = p.location
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


def test_whole_app_dark_existing_new_widgets_charts_and_light_restore(own, theme):
    current, store = theme
    w = own(create_window())
    query = w.query_state.draft
    assert "settings" in w.pages and w.nav["settings"].isEnabled()
    dashboard = w.pages["dashboard"]
    assert dashboard.content.parentWidget() is dashboard.overview
    personal = {**store.personal(), "home": "heatmap", "order": tuple(reversed(MENU_ORDER))}
    w.pages["settings"].personal_changed.emit(personal)
    assert dashboard.layout().indexOf(dashboard.overview) < dashboard.layout().indexOf(dashboard.kpi_grid)
    assert dashboard.content.parentWidget() is dashboard.overview
    assert w.personal_menu_layout.itemAt(0).widget() is w.nav["replay"]
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
