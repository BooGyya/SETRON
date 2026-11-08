"""B-2: 실제 릴리즈 필드를 허용하지 않는 초안·비동기 저장 흐름 검사."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from setron.ui.app import create_window
from setron.ui.theme import install_theme
from setron.ui.version_edit_data import EditDraft, EditField, SaveOutcome
from setron.ui.version_edit_dialog import VersionEditDialog


def validate_example(value):
    """업무 필드가 아닌 테스트 문자열의 빈 값을 거부한다.

    param
    - value: 검사할 테스트 값.

    return
    - str | None: 빈 값 오류 또는 통과.
    """
    return None if value.strip() else "테스트 값을 입력하세요."


class FakeGateway:
    """DB 없이 요청과 완료 함수를 보관하는 테스트 전용 저장 경계."""

    def __init__(self):
        """검토된 요청과 콜백 목록을 초기화한다."""
        self.calls = []

    def submit(self, request, complete):
        """완료를 지연해 저장 중 상태를 검증할 수 있게 한다.

        param
        - request, complete: 전달받은 요청과 완료 콜백.
        """
        self.calls.append((request, complete))


@pytest.fixture(scope="module")
def app():
    """공통 테마를 설치한 Qt 앱을 제공한다.

    return
    - QApplication: 테스트 공용 앱.
    """
    application = QApplication.instance() or QApplication([])
    install_theme(application)
    return application


@pytest.fixture
def dialog(app, monkeypatch):
    """테스트 전용 항목과 가짜 저장 서비스를 연결한다.

    param
    - app, monkeypatch: Qt 앱과 취소 응답 교체 도구.

    return
    - VersionEditDialog: 종료 시 폐기되는 테스트 편집 화면.
    """
    fields = (EditField("example_only", "테스트 항목", "원본 <값>", "현재값", validate_example),)
    widget = VersionEditDialog(EditDraft(101, fields), title="테스트 릴리즈", gateway=FakeGateway())
    widget.show()
    yield widget
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    widget.state = "edit"
    widget.reject()
    widget.deleteLater()
    app.processEvents()


def review_change(dialog):
    """테스트 항목을 변경하고 검토 화면으로 이동한다.

    param
    - dialog: 테스트 편집 화면.
    """
    dialog.inputs["example_only"].setText("수정값")
    dialog.check.click()
    assert dialog.state == "review"


def test_production_edit_has_reviewable_local_draft_without_mutating_source(app):
    """VER5의 실제 입력 폼을 열어도 취소 전후 A 조건·릴리즈 원본을 보존한다.

    param
    - app: Qt 앱.
    """
    window = create_window()
    window.show()
    window.navigate("versions")
    page = window.pages["versions"]
    page.open_row(0)
    before = page.catalog, page.selected_id, page.page_index, page.search.text(), window.query_state.applied
    page.edit_button.click()
    editor = page.release_form
    assert editor.isVisible() and page.gateway is None
    assert editor.raw.text() == page.catalog.entry(page.selected_id).release.version_raw
    assert editor.prepared is None and editor.save.isHidden()
    editor.raw.setText("검토 전 작업본")
    editor.cancel.click()
    assert not editor.isVisible() and not page.workspace.history
    assert before == (page.catalog, page.selected_id, page.page_index, page.search.text(), window.query_state.applied)
    assert page.mode == "detail"
    window.close()
    window.deleteLater()
    app.processEvents()


def test_review_snapshot_preserves_original_and_only_changed_fields(dialog):
    """원본과 관리값을 보존하고 변경 항목만 요청에 담는다.

    param
    - dialog: 테스트 편집 화면.
    """
    assert not dialog.check.isEnabled()
    review_change(dialog)
    assert dialog.reviewed.changes == (("example_only", "현재값", "수정값"),)
    assert dialog.draft.fields[0].original == "원본 <값>"
    assert dialog.draft.fields[0].current == "현재값"
    assert dialog.review_model.rowCount() == 1
    dialog.edit.click()
    dialog.inputs["example_only"].setText("현재값")
    assert dialog.reviewed is None and not dialog.check.isEnabled()


def test_validation_blocks_review_and_clears_on_edit(dialog):
    """빈 입력 오류를 표시하고 수정 후 검토할 수 있는지 검사한다.

    param
    - dialog: 테스트 편집 화면.
    """
    dialog.inputs["example_only"].setText("")
    dialog.check.click()
    assert dialog.state == "edit" and dialog.errors["example_only"].text()
    review_change(dialog)
    assert not dialog.errors["example_only"].text()


def test_disconnected_gateway_cannot_save(dialog):
    """항목이 있더라도 저장 연결이 없으면 저장하지 않는다.

    param
    - dialog: 테스트 편집 화면.
    """
    dialog.gateway = None
    review_change(dialog)
    assert not dialog.save.isEnabled()
    dialog._save()
    assert dialog.state == "review"


def test_duplicate_save_and_closing_while_pending_are_blocked(dialog, app):
    """저장 대기 중 중복 제출과 닫기를 차단하고 성공 뒤 재저장을 막는다.

    param
    - dialog, app: 테스트 편집 화면과 Qt 앱.
    """
    review_change(dialog)
    dialog.save.click()
    dialog._save()
    dialog.close()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.state == "saving" and dialog.isVisible()
    assert len(dialog.gateway.calls) == 1
    assert not dialog.inputs["example_only"].isEnabled()
    dialog.gateway.calls[0][1](SaveOutcome("success"))
    app.processEvents()
    assert dialog.state == "success"
    dialog._save()
    assert len(dialog.gateway.calls) == 1


def test_failure_preserves_draft_retry_ignores_old_response(dialog, app):
    """확정 실패 후 재검토하며 이전 요청의 늦은 응답은 무시한다.

    param
    - dialog, app: 테스트 편집 화면과 Qt 앱.
    """
    review_change(dialog)
    dialog.save.click()
    first = dialog.gateway.calls[0][1]
    first(SaveOutcome("failed"))
    app.processEvents()
    assert dialog.state == "failed"
    assert dialog.inputs["example_only"].text() == "수정값"
    dialog._save()
    assert len(dialog.gateway.calls) == 1
    dialog.check.click()
    dialog.save.click()
    first(SaveOutcome("success"))
    app.processEvents()
    assert dialog.state == "saving"
    dialog.gateway.calls[1][1](SaveOutcome("success"))
    app.processEvents()
    assert dialog.state == "success"


@pytest.mark.parametrize("outcome", [SaveOutcome("unknown"), None, SaveOutcome("invalid")])
def test_unknown_or_invalid_outcome_never_retries(dialog, app, outcome):
    """저장 여부가 불명확하면 초안을 보존하고 재제출을 막는다.

    param
    - dialog, app, outcome: 화면·Qt 앱·저장 결과.
    """
    review_change(dialog)
    dialog.save.click()
    dialog.gateway.calls[0][1](outcome)
    app.processEvents()
    assert dialog.state == "unknown"
    assert dialog.draft.values["example_only"] == "수정값"
    dialog._save()
    assert len(dialog.gateway.calls) == 1


def test_submission_exception_is_unknown(dialog, app, monkeypatch):
    """제출 예외는 미저장으로 단정하지 않고 결과 미확인으로 처리한다.

    param
    - dialog, app, monkeypatch: 화면·Qt 앱·예외 주입 도구.
    """
    def fail(*args):
        """저장 경계의 예외를 재현한다.

        param
        - args: 사용하지 않는 제출 인수.

        raises
        - RuntimeError: 테스트용 전송 오류.
        """
        raise RuntimeError("private backend details")
    monkeypatch.setattr(dialog.gateway, "submit", fail)
    review_change(dialog)
    dialog.save.click()
    app.processEvents()
    assert dialog.state == "unknown"
    assert "private" not in dialog.notice.text()


@pytest.mark.parametrize("close", [False, True])
def test_cancel_confirmation_keeps_or_discards_draft(dialog, monkeypatch, close):
    """취소 버튼과 창 닫기가 같은 확인 및 폐기 규칙을 적용한다.

    param
    - dialog, monkeypatch, close: 화면·응답 교체·창 닫기 여부.
    """
    review_change(dialog)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    dialog.close() if close else dialog.cancel.click()
    assert dialog.isVisible() and dialog.draft.request().changes
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    dialog.close() if close else dialog.cancel.click()
    assert not dialog.isVisible() and not dialog.draft.request().changes
    assert not dialog.gateway.calls


def test_duplicate_field_contract_rejected():
    """중복 필드가 초안이나 요청을 덮어쓰지 못하게 한다."""
    field = EditField("example", "테스트", None, "", validate_example)
    with pytest.raises(ValueError):
        EditDraft(1, (field, field))



def test_success_closes_as_accepted(dialog, app):
    """성공 후 닫기를 취소 결과와 구분한다.

    param
    - dialog, app: 편집 화면과 Qt 앱.
    """
    review_change(dialog)
    dialog.save.click()
    dialog.gateway.calls[0][1](SaveOutcome("success"))
    app.processEvents()
    dialog.cancel.click()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.reviewed.changes == (("example_only", "현재값", "수정값"),)


def test_worker_completion_is_queued_to_gui(dialog, app):
    """워커 응답이 UI 처리 전까지 상태를 직접 변경하지 않는지 검사한다.

    param
    - dialog, app: 편집 화면과 Qt 앱.
    """
    from threading import Thread
    review_change(dialog)
    dialog.save.click()
    worker = Thread(target=dialog.gateway.calls[0][1], args=(SaveOutcome("success"),))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert dialog.state == "saving"
    app.processEvents()
    assert dialog.state == "success"


def test_validator_exception_and_original_unknown_are_not_silently_accepted():
    """검증기 실패를 오류로 남기고 원본 미연결을 빈 값과 구분한다."""
    def fail(value):
        """테스트 검증 오류를 발생시킨다.

        param
        - value: 검사할 입력.

        raises
        - ValueError: 테스트용 검증기 실패.
        """
        raise ValueError("private")
    draft = EditDraft(1, (EditField("example", "테스트", None, "", fail),))
    draft.values["example"] = "new"
    draft.values["not_allowed"] = "ignored"
    assert draft.fields[0].original is None and draft.fields[0].current == ""
    assert draft.request().changes == (("example", "", "new"),)
    assert draft.errors() == {"example": "입력 검증을 완료하지 못했습니다."}
