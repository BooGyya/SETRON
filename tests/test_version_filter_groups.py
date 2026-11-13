"""설비군별 버전 표시는 바꾸되, 전체 조회의 버전 선택은 보존한다."""

from datetime import date
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.ui.query_state import GroupOption, QueryCatalog, VersionFilter
from setron.ui.theme import install_theme
from setron.ui.version_filter import version_group_options, version_options
from setron.ui.version_filter_dialog import VersionFilterDialog


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


@pytest.fixture
def source():
    def record(identity, pms):
        return EquipmentRecord(identity, f"TEST-{identity}", date(2026, 10, 21), "합성 사업장", None, "합성 모델", tuple(pms))
    return SimpleNamespace(
        catalog=QueryCatalog(groups=(GroupOption("c4", "C4"), GroupOption("o2", "O2"),
                                     GroupOption("both", "복합 설비군"), GroupOption("empty", "미관측 설비군"))),
        bindings={"c4": (101,), "o2": (102,), "both": (101, 102), "empty": (103,)},
        records=(
            record(1, (PmVersion("PM1", 101, 1001, "v1"), PmVersion("PM2", 102, 1002, "v2"))),
            record(2, (PmVersion("PM1", 101, 1001, "shared"), PmVersion("PM2", 102, 1002, "shared"))),
            record(3, (PmVersion(None, 101, 1001, None),)),
            record(4, (PmVersion(None, 102, 1002, "   "),)),
            record(5, (PmVersion(None, 102, 1002, " v2 "),)),
            record(6, (PmVersion(None, 999, 1003, "orphan"),)),
            record(7, ()),
        ),
    )


@pytest.fixture
def open_dialog(app, source):
    dialogs = []
    def create(original=None):
        options = version_options(source)
        dialog = VersionFilterDialog(options, original, group_options=version_group_options(source, options))
        dialogs.append(dialog)
        dialog.show()
        QTest.qWait(20)
        return dialog
    yield create
    for dialog in dialogs:
        dialog.reject()
        dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def select_group(dialog, key):
    index = dialog.group_filter.findData(key)
    assert index >= 0
    dialog.group_filter.setCurrentIndex(index)
    QApplication.processEvents()


def visible_versions(dialog):
    return {value for value, control in dialog.choices.items() if control.isVisibleTo(dialog)}


@pytest.mark.parametrize("options, expected", [
    (None, (("c4", "C4", ("shared", "v1", None)),
            ("o2", "O2", (" v2 ", "shared", "v2", None)),
            ("both", "복합 설비군", (" v2 ", "shared", "v1", "v2", None)),
            ("empty", "미관측 설비군", ()))),
    (("v2", None, "v1", "shared"), (("c4", "C4", (None, "v1", "shared")),
                                    ("o2", "O2", ("v2", None, "shared")),
                                    ("both", "복합 설비군", ("v2", None, "v1", "shared")),
                                    ("empty", "미관측 설비군", ()))),
])
def test_group_options_follow_model_bindings_and_preserve_raw_values_and_option_order(source, options, expected):
    before = tuple(tuple(pm.version_raw for pm in record.pms) for record in source.records)
    assert version_options(source) == (" v2 ", "orphan", "shared", "v1", "v2", None)
    assert version_group_options(source, options) == expected
    assert tuple(tuple(pm.version_raw for pm in record.pms) for record in source.records) == before


def test_group_bulk_toggle_only_changes_visible_versions_and_keeps_hidden_selections(open_dialog):
    original = VersionFilter(("lost", "orphan"), include_missing=True)
    dialog = open_dialog(original)
    accepted = QSignalSpy(dialog.accepted)
    shared = dialog.choices["shared"]
    select_group(dialog, "c4")
    assert visible_versions(dialog) == {"shared", "v1", None}
    assert dialog.choices["lost"].isChecked() and dialog.choices["orphan"].isChecked()
    assert dialog.all_button.text() == "전체 선택"
    QTest.mouseClick(dialog.all_button, Qt.MouseButton.LeftButton)
    assert all(dialog.choices[value].isChecked() for value in ("shared", "v1", None))
    QTest.mouseClick(dialog.all_button, Qt.MouseButton.LeftButton)
    assert not any(dialog.choices[value].isChecked() for value in ("shared", "v1", None))
    assert dialog.count.text() == "2개 선택 중" and dialog.confirm_button.isEnabled()
    select_group(dialog, "o2")
    assert visible_versions(dialog) == {" v2 ", "shared", "v2", None}
    assert dialog.choices["shared"] is shared and not shared.isChecked()
    selected = dialog.choices["v2"]
    selected.setFocus()
    QTest.keyClick(selected, Qt.Key.Key_Space)
    select_group(dialog, None)
    assert "lost" in visible_versions(dialog) and dialog.choices["lost"].isChecked()
    select_group(dialog, "c4")
    assert dialog.count.text() == "3개 선택 중" and dialog.confirm_button.isEnabled()
    QTest.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert accepted.count() == 1
    assert dialog.result_filter == VersionFilter(("lost", "orphan", "v2"))
    assert original == VersionFilter(("lost", "orphan"), include_missing=True)


def test_empty_group_does_not_clear_or_widen_global_selection(open_dialog):
    original = VersionFilter(("orphan",))
    dialog = open_dialog(original)
    select_group(dialog, "empty")
    assert visible_versions(dialog) == set() and dialog.empty_group.isVisible()
    assert not dialog.all_button.isEnabled() and dialog.confirm_button.isEnabled()
    QTest.mouseClick(dialog.all_button, Qt.MouseButton.LeftButton)
    assert dialog.count.text() == "1개 선택 중" and dialog.choices["orphan"].isChecked()
    QTest.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert dialog.result_filter == original


def test_selecting_all_in_one_group_remains_a_restricted_version_filter(open_dialog):
    dialog = open_dialog(VersionFilter(("v1",)))
    select_group(dialog, "c4")
    QTest.mouseClick(dialog.all_button, Qt.MouseButton.LeftButton)
    assert all(dialog.choices[value].isChecked() for value in ("shared", "v1", None))
    assert not dialog.choices["v2"].isChecked() and not dialog.choices["orphan"].isChecked()
    QTest.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert dialog.result_filter == VersionFilter(("shared", "v1"), include_missing=True)


def test_canceling_group_filtered_edits_does_not_commit_temporary_selection(open_dialog):
    original = VersionFilter(("v1",))
    dialog = open_dialog(original)
    accepted = QSignalSpy(dialog.accepted)
    select_group(dialog, "o2")
    QTest.mouseClick(dialog.all_button, Qt.MouseButton.LeftButton)
    assert dialog.choices["v2"].isChecked() and dialog.choices[" v2 "].isChecked()
    QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Rejected and accepted.count() == 0
    assert dialog.result_filter is None and original == VersionFilter(("v1",))
