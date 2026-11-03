"""재가져오기에서 연결할 수 없는 수동 위험도 확인·취소 후 미확인으로 보존한다."""

from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from setron.features.import_save.service import DatabaseImportSaver
from setron.ui.release_import_review import choose_release_reviews
from test_release_import_integration import import_request, reviewed_request
from test_release_replacement import original
from test_release_revision import all_rows, revision_storage
from test_ui_import import app


@pytest.fixture
def missing_risk(revision_storage):
    """새 노트에 없는 v2에 사용자가 직접 지정한 위험을 준비한다."""
    storage = revision_storage
    original(storage)
    removed = original(storage, raw="v2", number=8, order=2)
    with storage.transaction() as tx:
        rule = tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": removed.release.current["release_version_id"],
            "risk_reason": "고객 확인 필요 결함", "target_reference": {}, "mapping_status": "VERIFIED"})
    saver = DatabaseImportSaver(storage)
    request = reviewed_request(saver, import_request(storage))
    plans = saver.preview_release_import(request, request.release_reviews)
    assert plans[0].preview.risk_reconnections[0].can_restore is False
    return storage, saver, request, plans, rule["risk_rule_id"]


def test_missing_risk_target_is_explained_and_preserved_unresolved(app, missing_risk, monkeypatch):
    """실제 교체 후보의 근거를 안내하고 진행해도 위험을 강제 복원하지 않는다."""
    storage, saver, request, plans, rule_id = missing_risk
    messages = []

    def acknowledge(parent, title, message, buttons, default):
        """사용자가 미확인 보존 안내를 읽고 저장을 계속한다."""
        messages.append(message)
        assert default is QMessageBox.StandardButton.Cancel
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", acknowledge)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("복원 불가 위험을 연결하려 했습니다."))
    decisions = set()
    chosen, again = choose_release_reviews(None, plans, request.release_reviews, risk_decisions=decisions)
    assert again is False and chosen[0].risk_confirmations == () and decisions == {rule_id}
    assert len(messages) == 1
    assert all(word in messages[0] for word in ("v2", "고객 확인 필요 결함", "미확인으로 보존", "확인할 내용"))
    receipt = saver.save(replace(request, release_reviews=chosen))
    assert receipt.refresh.unresolved_risk_ids == (rule_id,)
    assert all_rows(storage, "risk_rule")[0]["mapping_status"] == "UNRESOLVED"


def test_missing_risk_warning_can_cancel_without_approving_or_writing(app, missing_risk, monkeypatch):
    """미확인 위험을 확인하던 중 취소하면 교체를 진행하지 않는다."""
    storage, saver, request, plans, rule_id = missing_risk
    before = {name: all_rows(storage, name) for name in ("release_version", "item", "risk_rule", "import_batch")}
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Cancel)
    decisions = set()
    assert choose_release_reviews(None, plans, request.release_reviews, risk_decisions=decisions) == (None, False)
    assert not decisions
    assert {name: all_rows(storage, name) for name in before} == before


def test_acknowledged_missing_risk_is_not_asked_again_or_confirmed(app, missing_risk, monkeypatch):
    """다른 계열 검토로 재호출돼도 같은 미확인 위험의 안내를 반복하지 않는다."""
    storage, saver, request, plans, rule_id = missing_risk
    calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: calls.append(args) or QMessageBox.StandardButton.Ok)
    decisions = set()
    chosen, _ = choose_release_reviews(None, plans, request.release_reviews, risk_decisions=decisions)
    chosen, again = choose_release_reviews(None, plans, chosen, risk_decisions=decisions)
    assert len(calls) == 1 and again is False and chosen[0].risk_confirmations == ()


def test_verified_unchanged_risk_does_not_need_extra_warning(app, revision_storage, monkeypatch):
    """대상이 명확히 유지된 위험에는 복원 불가 경고를 표시하지 않는다."""
    first = original(revision_storage)
    with revision_storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1,
            "release_version_id": first.release.current["release_version_id"],
            "risk_reason": "그대로 유지할 위험", "target_reference": {}, "mapping_status": "VERIFIED"})
    saver = DatabaseImportSaver(revision_storage)
    request = reviewed_request(saver, import_request(revision_storage))
    plans = saver.preview_release_import(request, request.release_reviews)
    proposal = plans[0].preview.risk_reconnections[0]
    assert proposal.can_restore is True and proposal.requires_confirmation is False
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail("확인된 위험에 경고가 표시됐습니다."))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("그대로 유지할 위험을 재확인했습니다."))
    chosen, again = choose_release_reviews(None, plans, request.release_reviews)
    assert again is False and chosen == request.release_reviews
