"""공통 기준과 자동 판단을 표시하고 사용자 재선택을 검토한 뒤 적용한다."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog

from setron.ui.combo_box import ComboBox
from setron.ui.import_dialogs import ImportConfirmDialog
from setron.ui.sql_import_page import SqlImportPage
from setron.ui.sql_import_review import BASELINE_COLUMN, CHOICE_COLUMN, SqlComparisonWidget
from test_ui_import_ver5 import own, until  # noqa: F401
from test_peer_exchange import PEERS, delete_snapshot, exchange_file, peers, stamp  # noqa: F401
from test_sql_import_service import bundle, current
from test_ui_sql_peer_integration import until as wait_for


def difference(identifier, current, incoming, baseline=None, *, has_baseline=True, choice=None,
               automatic=False, reason="", field=None, requires_confirmation=False):
    return SimpleNamespace(id=identifier, table="equipment_snapshot", label="EQ-1 · 2026-11-01",
                           key={"snapshot_id": 1}, field=field or identifier, grouped=False,
                           current=current, incoming=incoming, baseline=baseline, has_baseline=has_baseline,
                           choice=choice, automatic=automatic, reason=reason, requires_confirmation=requires_confirmation)


class Gateway:
    def __init__(self, *, conflict=False):
        self.calls = []
        self.conflict = conflict

    def make_plan(self, choices=None):
        choices = dict(choices or {})
        values = [
            difference("line", "이 PC 라인", "기준 라인", "기준 라인", choice=choices.get("line", "CURRENT"),
                       automatic="line" not in choices, reason="현재 PC만 변경하여 현재값을 유지합니다."),
            difference("process_name", "기준 공정", "다른 PC 공정", "기준 공정",
                       choice=choices.get("process_name", "INCOMING"), automatic="process_name" not in choices,
                       reason="가져온 자료만 변경하여 가져온값을 사용합니다."),
        ]
        if self.conflict:
            values.append(difference("content", "이 PC 수정", "다른 PC 수정", "기준 설명",
                                     choice=choices.get("content"), reason="양쪽에서 서로 다른 값으로 변경했습니다."))
        return SimpleNamespace(mode="review", differences=tuple(values), choices=choices,
                               unresolved=sum(item.choice is None for item in values), changes=2, target_rows=3,
                               automatic_count=sum(item.automatic for item in values),
                               summary="대상 DB · 기존 3행 · 갱신 2행")

    def preview(self, *_):
        self.calls.append(("preview",))
        return self.make_plan()

    def resolve(self, original, choices):
        self.calls.append(("resolve", dict(choices)))
        selected = dict(original.choices)
        for identifier, choice in choices.items():
            if choice == "AUTO":
                selected.pop(identifier, None)
            else:
                selected[identifier] = choice
        return self.make_plan(selected)

    def apply(self, plan):
        assert plan.unresolved == 0
        self.calls.append(("apply", plan))
        return plan


def page(own, tmp_path, monkeypatch, *, conflict=False):  # noqa: F811
    gateway = Gateway(conflict=conflict)
    widget = own(SqlImportPage(gateway))
    widget.resize(1100, 1000)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(tmp_path / "peer.sql"), ""))
    widget.select_button.click()
    widget.preview_button.click()
    until(lambda: not widget.busy)
    return widget, gateway


def choose(widget, row, choice):
    assert widget.comparison.model.setData(widget.comparison.model.index(row, CHOICE_COLUMN), choice,
                                           Qt.ItemDataRole.EditRole)


def test_common_baseline_distinguishes_missing_baseline_from_a_known_empty_value(own):
    widget = own(SqlComparisonWidget())
    widget.set_differences((difference("line", None, "새 값", None, choice="INCOMING", automatic=True),
                            difference("content", "기존", "가져온", has_baseline=False)))
    assert widget.model.columnCount() == 7
    assert widget.model.data(widget.model.index(0, BASELINE_COLUMN)) == "(없음)"
    assert widget.model.data(widget.model.index(1, BASELINE_COLUMN)) == "공통 기준 없음"
    assert widget.baseline_detail.toPlainText() == "(없음)"
    widget.table.setCurrentIndex(widget.proxy.index(1, 0))
    assert widget.baseline_detail.toPlainText() == "공통 기준 없음"
    assert not widget.model.setData(widget.model.index(1, CHOICE_COLUMN), "AUTO")


def test_automatic_choices_are_ready_for_confirmation_without_becoming_manual(own, tmp_path, monkeypatch):
    widget, gateway = page(own, tmp_path, monkeypatch)
    assert widget.comparison.automatic_count == 2 and widget.comparison.manual_count == 0
    assert widget.comparison.manual_choices == {}
    assert "자동 2건" in widget.comparison.count_label.text()
    assert "자동 · 현재값 유지" == widget.comparison.model.data(widget.comparison.model.index(0, CHOICE_COLUMN))
    assert "현재 PC만 변경" in widget.comparison.reason_detail.text()
    assert widget.apply_button.isEnabled() and not widget.comparison.review_button.isEnabled()

    def confirm(dialog):
        assert "자동 2건" in dialog.message.text() and "수동 선택 0건" in dialog.message.text()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ImportConfirmDialog, "exec", confirm)
    widget.apply_button.click()
    until(lambda: not widget.busy)
    assert [call[0] for call in gateway.calls] == ["preview", "apply"]
    assert widget.status.text() == "가져오기 완료"


def test_only_conflict_selection_is_sent_with_unchanged_automatic_rows(own, tmp_path, monkeypatch):
    widget, gateway = page(own, tmp_path, monkeypatch, conflict=True)
    assert widget.comparison.automatic_count == 2 and widget.comparison.unresolved == 1
    assert not widget.apply_button.isEnabled()
    choose(widget, 2, "CURRENT")
    assert widget.comparison.review_button.isEnabled() and not widget.apply_button.isEnabled()
    widget.comparison.review_button.click()
    until(lambda: not widget.busy)
    assert gateway.calls[-1] == ("resolve", {"content": "CURRENT"})
    assert widget.comparison.automatic_count == 2 and widget.comparison.manual_count == 1
    assert widget.apply_button.isEnabled()


def test_manual_override_and_return_to_automatic_require_fresh_review(own, tmp_path, monkeypatch):
    widget, gateway = page(own, tmp_path, monkeypatch)
    choose(widget, 0, "INCOMING")
    assert widget.comparison.automatic_count == 1 and widget.comparison.manual_count == 1
    assert not widget.apply_button.isEnabled() and widget.comparison.review_button.isEnabled()
    widget.comparison.review_button.click()
    until(lambda: not widget.busy)
    assert gateway.calls[-1] == ("resolve", {"line": "INCOMING"})
    assert widget.apply_button.isEnabled()
    choose(widget, 0, "AUTO")
    assert widget.comparison.manual_choices == {"line": "AUTO"}
    assert not widget.apply_button.isEnabled() and widget.comparison.review_button.isEnabled()
    widget.comparison.review_button.click()
    until(lambda: not widget.busy)
    assert gateway.calls[-1] == ("resolve", {"line": "AUTO"})
    assert widget.comparison.automatic_count == 2 and widget.comparison.manual_count == 0
    assert widget.comparison.choices["line"] == "CURRENT" and widget.apply_button.isEnabled()


def test_return_to_automatic_conflict_becomes_unresolved_and_cannot_apply(own, tmp_path, monkeypatch):
    widget, gateway = page(own, tmp_path, monkeypatch, conflict=True)
    choose(widget, 2, "CURRENT")
    widget.comparison.review_button.click()
    until(lambda: not widget.busy)
    assert widget.apply_button.isEnabled()
    choose(widget, 2, "AUTO")
    widget.comparison.review_button.click()
    until(lambda: not widget.busy)
    assert gateway.calls[-1] == ("resolve", {"content": "AUTO"})
    assert widget.comparison.unresolved == 1 and not widget.apply_button.isEnabled()
    widget._confirm_apply()
    assert gateway.calls[-1][0] == "resolve"


def test_clearing_an_automatic_value_cannot_apply_the_old_complete_plan(own, tmp_path, monkeypatch):
    widget, _ = page(own, tmp_path, monkeypatch)
    choose(widget, 0, None)
    assert widget.comparison.unresolved == 1 and not widget.apply_button.isEnabled()
    assert not widget.comparison.review_button.isEnabled()


def test_bulk_explicit_selection_is_reviewed_even_if_it_equals_one_automatic_value(own, tmp_path, monkeypatch):
    widget, gateway = page(own, tmp_path, monkeypatch)
    widget.comparison.keep_button.click()
    assert widget.comparison.automatic_count == 0 and widget.comparison.manual_count == 2
    assert not widget.apply_button.isEnabled()
    widget.comparison.review_button.click()
    until(lambda: not widget.busy)
    assert gateway.calls[-1] == ("resolve", {"line": "CURRENT", "process_name": "CURRENT"})
    assert widget.comparison.automatic_count == 0 and widget.comparison.manual_count == 2
    assert widget.apply_button.isEnabled()


def test_automatic_reset_menu_only_appears_when_a_common_baseline_exists(own):
    widget = own(SqlComparisonWidget())
    widget.set_differences((difference("line", "A", "B", "OLD", choice="CURRENT", automatic=True),
                            difference("content", "A", "B", has_baseline=False)))
    for row, expected in ((0, True), (1, False)):
        index = widget.proxy.index(row, CHOICE_COLUMN)
        widget.table.setCurrentIndex(index)
        widget.table.edit(index)
        editor = widget.table.findChild(ComboBox)
        assert editor is not None
        assert (editor.findData("AUTO") >= 0) is expected
        widget.table.closePersistentEditor(index)
        widget.table.setFocus()
        from PySide6.QtTest import QTest
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        QTest.qWait(5)


@pytest.mark.parametrize("field,protected", [("삭제", False), ("PM·SW 구성", True), ("N 그룹·저장 이력", True)])
def test_bulk_incoming_does_not_confirm_deletion_or_destructive_group_for_the_user(own, field, protected):
    """일괄 선택은 일반 수정만 고르고, 삭제되는 자료는 개별 선택을 기다린다."""
    widget = own(SqlComparisonWidget())
    widget.set_differences((difference("update", "OLD", "NEW", "OLD", has_baseline=False),
                            difference("delete", {"현재 자료": ["A", "B"]}, "삭제", {"현재 자료": ["A", "B"]},
                                       field=field, requires_confirmation=protected)))
    widget.incoming_button.click()
    assert widget.choices == {"update": "INCOMING"}
    assert widget.unresolved == 1 and widget.individual_confirmations == 1
    assert "개별 확인 필요 1건" in widget.count_label.text()
    assert widget.confirmation_note.isVisible() and "일괄 선택에서 제외" in widget.confirmation_note.text()
    assert widget.model.setData(widget.model.index(1, CHOICE_COLUMN), "INCOMING")
    assert widget.unresolved == 0 and widget.individual_confirmations == 0
    widget.incoming_button.click()
    assert widget.choices == {"update": "INCOMING", "delete": "INCOMING"}


def test_bulk_incoming_preserves_a_protected_rows_individual_current_choice(own):
    """직접 현재값 유지를 선택한 삭제 항목은 뒤의 일괄 가져온값 선택으로 바뀌지 않는다."""
    widget = own(SqlComparisonWidget())
    widget.set_differences((difference("update", "OLD", "NEW", "OLD", has_baseline=False),
                            difference("delete", "설비 자료", "삭제", "설비 자료", field="삭제")))
    assert widget.model.setData(widget.model.index(1, CHOICE_COLUMN), "CURRENT")
    widget.incoming_button.click()
    assert widget.choices == {"update": "INCOMING", "delete": "CURRENT"}
    assert widget.unresolved == 0 and widget.individual_confirmations == 0


def real_page(own, peers, tmp_path, monkeypatch, *, local_change=False):
    source, destination = peers("A", initial=bundle()), peers("B")
    stamp(source, PEERS[0])
    filename = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(destination[2].preview(filename, "review"))
    with source[0].begin() as connection:
        connection.execute(source[1]["equipment_snapshot"].update().values(line="가져온 라인"))
    if local_change:
        with destination[0].begin() as connection:
            connection.execute(destination[1]["equipment_snapshot"].update().values(line="이 PC의 새 라인"))
    stamp(source, PEERS[0])
    filename = exchange_file(source, tmp_path / "changed.sql")
    widget = own(SqlImportPage(destination[2]))
    widget.resize(1100, 1000)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(filename), ""))
    widget.select_button.click()
    widget.preview_button.click()
    wait_for(lambda: not widget.busy)
    assert widget.plan is not None, widget.details.text()
    return widget, destination


def test_real_service_one_sided_change_shows_baseline_and_applies_automatic_value(own, peers, tmp_path, monkeypatch):
    """실제 공유 이력의 한쪽 변경은 자동 선택되고 확인 후 같은 DB 행을 갱신한다."""
    widget, destination = real_page(own, peers, tmp_path, monkeypatch)
    line, = [item for item in widget.plan.differences if item.field == "line"]
    assert line.has_baseline and line.baseline == "기존 라인"
    assert line.automatic and line.choice == "INCOMING"
    assert "기존 라인" == widget.comparison.baseline_detail.toPlainText()
    assert widget.apply_button.isEnabled() and not widget.comparison.review_button.isEnabled()
    monkeypatch.setattr(ImportConfirmDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    widget.apply_button.click()
    wait_for(lambda: not widget.busy)
    assert widget.status.text() == "가져오기 완료"
    assert current(destination)["equipment_snapshot"][0]["line"] == "가져온 라인"


def test_real_service_divergent_change_requires_explicit_resolution(own, peers, tmp_path, monkeypatch):
    """내보내기 전 로컬 수정도 공통 기준과 대조해 충돌로 보이고 수동 선택으로 보존한다."""
    widget, destination = real_page(own, peers, tmp_path, monkeypatch, local_change=True)
    row, line = next((index, item) for index, item in enumerate(widget.plan.differences) if item.field == "line")
    assert line.has_baseline and line.baseline == "기존 라인"
    assert not line.automatic and line.choice is None
    assert not widget.apply_button.isEnabled()
    choose(widget, row, "CURRENT")
    widget.comparison.review_button.click()
    wait_for(lambda: not widget.busy)
    assert widget.apply_button.isEnabled()
    monkeypatch.setattr(ImportConfirmDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    widget.apply_button.click()
    wait_for(lambda: not widget.busy)
    assert widget.status.text() == "가져오기 완료"
    assert current(destination)["equipment_snapshot"][0]["line"] == "이 PC의 새 라인"


def test_real_service_deletion_is_skipped_by_bulk_then_applied_after_individual_confirmation(own, peers, tmp_path, monkeypatch):
    """실제 삭제 이력은 일괄 선택으로 적용되지 않고 개별 확인 뒤에만 DB에서 제거된다."""
    source, destination = peers("A", initial=bundle()), peers("B")
    stamp(source, PEERS[0])
    filename = exchange_file(source, tmp_path / "initial.sql")
    destination[2].apply(destination[2].preview(filename, "review"))
    delete_snapshot(source)
    stamp(source, PEERS[0])
    filename = exchange_file(source, tmp_path / "deleted.sql")
    widget = own(SqlImportPage(destination[2]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(filename), ""))
    widget.select_button.click()
    widget.preview_button.click()
    wait_for(lambda: not widget.busy)
    assert widget.plan is not None, widget.details.text()
    assert any(item.requires_confirmation for item in widget.plan.differences)
    widget.comparison.incoming_button.click()
    assert widget.comparison.individual_confirmations and not widget.apply_button.isEnabled()
    assert current(destination)["equipment_snapshot"]
    for index, item in enumerate(widget.plan.differences):
        if item.requires_confirmation:
            choose(widget, index, "INCOMING")
    widget.comparison.review_button.click()
    wait_for(lambda: not widget.busy)
    assert widget.apply_button.isEnabled()
    monkeypatch.setattr(ImportConfirmDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    widget.apply_button.click()
    wait_for(lambda: not widget.busy)
    assert widget.status.text() == "가져오기 완료"
    assert not current(destination)["equipment_snapshot"]
