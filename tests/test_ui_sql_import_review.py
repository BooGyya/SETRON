"""SQL의 비교·선택·재검토와 저장 경계를 실제 Qt 화면에서 확인한다."""

from datetime import date
from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog

from setron.features.db_import.service import SqlImportError
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.combo_box import ComboBox
from setron.ui.sql_import_page import SqlImportPage
from setron.ui.sql_import_review import SqlComparisonWidget, value_text
from test_ui_import_ver5 import own, until  # noqa: F401


def difference(identifier, field, current, incoming, *, grouped=False, choice=None):
    return SimpleNamespace(id=identifier, table="equipment_snapshot", label="EQ-1 · 2026-11-01", key={"snapshot_id": 1},
                           field=field, current=current, incoming=incoming, grouped=grouped, choice=choice)


def plan(choices=None, *, mode="review", changes=2):
    choices = choices or {}
    differences = (
        difference("line", "line", "현재 라인", "파일 라인", choice=choices.get("line")),
        difference("process", "process_name", "기존 공정", "새 공정", choice=choices.get("process")),
        difference("software", "PM·SW 구성", [{"family": "O2", "version": "v1", "pms": ["PM1"]}],
                   [{"family": "O2", "version": "v2", "pms": ["PM1", "PM2"]}], grouped=True,
                   choice=choices.get("software")),
    ) if mode == "review" else ()
    return SimpleNamespace(mode=mode, differences=differences, unresolved=sum(item.choice is None for item in differences),
                           changes=changes, target_rows=3, summary="대상 DB · 기존 3행 · 갱신 2행")


class Gateway:
    def __init__(self, *, gate=None, error=None):
        self.calls, self.gate, self.error = [], gate, error

    def preview(self, path, mode):
        self.calls.append(("preview", mode, get_ident()))
        return plan(mode=mode)

    def resolve(self, original, choices):
        self.calls.append(("resolve", dict(choices), get_ident()))
        if self.gate:
            assert self.gate.wait(4)
        if self.error:
            raise self.error
        return plan(choices)

    def apply(self, chosen):
        assert not chosen.unresolved
        self.calls.append(("apply", chosen, get_ident()))
        return chosen


def page(own, tmp_path, monkeypatch, gateway=None):  # noqa: F811
    gateway = gateway or Gateway()
    widget = own(SqlImportPage(gateway))
    widget.resize(1100, 900)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(tmp_path / "peer.sql"), ""))
    widget.select_button.click()
    widget.preview_button.click()
    until(lambda: not widget.busy)
    return widget, gateway


def choose(widget, row, value):
    assert widget.comparison.model.setData(widget.comparison.model.index(row, 4), value, Qt.ItemDataRole.EditRole)


def test_review_is_default_and_unresolved_choices_cannot_apply(own, tmp_path, monkeypatch):
    widget, gateway = page(own, tmp_path, monkeypatch)
    assert widget.mode_combo.currentData() == "review"
    assert gateway.calls[0][:2] == ("preview", "review")
    assert widget.comparison.isVisible() and widget.comparison.model.rowCount() == 3
    assert "선택 필요 3건" in widget.comparison.count_label.text()
    assert widget.status.text() == "적용할 값 선택 필요"
    assert not widget.apply_button.isEnabled() and not widget.comparison.review_button.isEnabled()
    widget._confirm_apply()
    assert len(gateway.calls) == 1
    assert "현재 라인" in widget.comparison.current_detail.toPlainText()
    assert "파일 라인" in widget.comparison.incoming_detail.toPlainText()
    assert not widget.comparison.model.flags(widget.comparison.model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable


def test_individual_selection_requires_background_validation_before_confirmed_apply(own, tmp_path, monkeypatch):
    gate = Event()
    widget, gateway = page(own, tmp_path, monkeypatch, Gateway(gate=gate))
    index = widget.comparison.proxy.index(0, 4)
    widget.comparison.table.setCurrentIndex(index)
    widget.comparison.table.edit(index)
    editor = widget.comparison.table.findChild(ComboBox)
    assert editor is not None
    editor.setCurrentIndex(editor.findData("CURRENT"))
    editor.activated.emit(editor.currentIndex())
    assert widget.comparison.choices == {"line": "CURRENT"}
    choose(widget, 1, "INCOMING")
    assert not widget.comparison.review_button.isEnabled()
    choose(widget, 2, "INCOMING")
    assert widget.comparison.review_button.isEnabled() and not widget.apply_button.isEnabled()
    widget.comparison.review_button.click()
    assert widget.busy and not widget.comparison.isEnabled()
    gate.set()
    until(lambda: not widget.busy)
    assert gateway.calls[-1][0] == "resolve" and gateway.calls[-1][2] != get_ident()
    assert gateway.calls[-1][1] == {"line": "CURRENT", "process": "INCOMING", "software": "INCOMING"}
    assert widget.apply_button.isEnabled() and not widget.comparison.review_button.isEnabled()
    assert widget.plan.unresolved == 0

    confirmed = False
    def confirm(dialog):
        assert "현재값 유지 1건" in dialog.message.text()
        assert "가져온값 사용 2건" in dialog.message.text()
        return QDialog.DialogCode.Accepted if confirmed else QDialog.DialogCode.Rejected
    monkeypatch.setattr(ImportConfirmDialog, "exec", confirm)
    widget.apply_button.click()
    assert gateway.calls[-1][0] == "resolve" and widget.apply_button.isEnabled()
    confirmed = True
    widget.apply_button.click()
    until(lambda: not widget.busy)
    assert gateway.calls[-1][0] == "apply" and widget.status.text() == "가져오기 완료"
    assert widget.plan is None and not widget.apply_button.isEnabled()


@pytest.mark.parametrize("choice,button_name", [("CURRENT", "keep_button"), ("INCOMING", "incoming_button")])
def test_bulk_choice_and_later_edits_invalidate_reviewed_plan(own, tmp_path, monkeypatch, choice, button_name):
    widget, _ = page(own, tmp_path, monkeypatch)
    getattr(widget.comparison, button_name).click()
    assert widget.comparison.choices == {key: choice for key in ("line", "process", "software")}
    assert not widget.comparison.unresolved and widget.comparison.review_button.isEnabled()
    widget.comparison.review_button.click()
    until(lambda: not widget.busy)
    assert widget.apply_button.isEnabled()
    choose(widget, 1, "INCOMING" if choice == "CURRENT" else "CURRENT")
    assert not widget.apply_button.isEnabled() and widget.comparison.review_button.isEnabled()
    assert widget.status.text() == "선택 내용 검토 필요"


def test_mode_change_clears_values_choices_and_stale_worker_completion(own, tmp_path, monkeypatch):
    gate = Event()
    widget, _ = page(own, tmp_path, monkeypatch, Gateway(gate=gate))
    widget.comparison.keep_button.click()
    widget.comparison.review_button.click()
    assert widget.busy
    widget.mode_combo.setCurrentIndex(widget.mode_combo.findData("add"))
    assert not widget.busy and widget.plan is None
    assert not widget.comparison.isVisible() and widget.comparison.choices == {}
    assert not widget.apply_button.isEnabled() and widget.status.text() == "검토 대기"
    gate.set()
    from PySide6.QtTest import QTest
    QTest.qWait(50)
    assert widget.plan is None and widget.status.text() == "검토 대기"


@pytest.mark.parametrize("error,expected", [
    (SqlImportError("대상 DB가 바뀌었습니다."), "대상 DB가 바뀌었습니다."),
    (RuntimeError("private-connection-token"), "선택한 값을 검토하지 못했습니다."),
])
def test_review_failure_keeps_choices_and_never_enables_apply(own, tmp_path, monkeypatch, error, expected):
    widget, gateway = page(own, tmp_path, monkeypatch, Gateway(error=error))
    widget.comparison.incoming_button.click()
    widget.comparison.review_button.click()
    until(lambda: not widget.busy)
    assert widget.status.text() == "선택 검토 실패" and widget.details.text() == expected
    assert widget.comparison.choices == {key: "INCOMING" for key in ("line", "process", "software")}
    assert not widget.apply_button.isEnabled() and widget.comparison.review_button.isEnabled()
    widget._confirm_apply()
    assert gateway.calls[-1][0] == "resolve"


def test_group_values_search_and_plain_text_detail_preserve_the_full_source(own):
    widget = own(SqlComparisonWidget())
    widget.set_differences((difference("html", "content", "<b>원문</b>\n둘째 줄", "가져온 값".encode()),
                            difference("group", "PM·SW 구성", [{"family": "O2", "version": "v1", "pms": ["PM1"]}],
                                       [{"family": "O2", "version": "v2"}], grouped=True)))
    assert widget.current_detail.toPlainText() == "<b>원문</b>\n둘째 줄"
    assert widget.incoming_detail.toPlainText() == "가져온 값"
    assert "(묶음)" in widget.model.data(widget.model.index(1, 1))
    assert "SW 계열: O2" in widget.model.data(widget.model.index(1, 2))
    widget.search.setText("PM1")
    assert widget.proxy.rowCount() == 1
    widget.keep_button.click()
    assert widget.choices == {"html": "CURRENT", "group": "CURRENT"}
    assert value_text(date(2026, 11, 17)) == "2026-11-17"
