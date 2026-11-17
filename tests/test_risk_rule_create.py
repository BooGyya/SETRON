"""위험 기준 새로 등록(BE-015)이 연결을 검사해 저장하고 위험 판정에 바로 반영되는지 검증한다."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from setron.features.management import ManagementService
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.risk.schemas import RiskStatus
from setron.ui.database_source import load_dashboard_source
from setron.ui.management_data import ManagementGateway
from setron.ui.management_page import ManagementPage
from setron.ui.risk_rule_dialog import RiskRuleDialog, risk_rule_options
from test_management_app_integration import file_storage  # noqa: F401  워커 스레드용 파일 DB
from test_management_service import observe
from test_release_revision import all_rows, release, revision_storage  # noqa: F401


def direct_rule(release_id, **changes):
    return {"rule_type": "DIRECT_VERSION", "family_id": 1, "release_version_id": release_id,
            "risk_reason": "검증용 위험 버전", "mapping_status": "VERIFIED", **changes}


def test_created_rule_is_saved_with_reference_and_marks_equipment_risky(revision_storage):  # noqa: F811
    storage = revision_storage
    with storage.transaction() as tx:  # 조회 화면 설비군(M-O2)에 연결되도록 등록 모델 이름을 맞춘다
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
    release_id = release(storage, raw="v1", order=1)
    snapshot = observe(storage, release_id)
    assert load_dashboard_source(storage).risks[snapshot.equipment_id] is RiskStatus.UNKNOWN  # 위험 기준 없음

    result = ManagementService(storage).create_risk_rule(direct_rule(release_id))

    created = result.records[0].current
    assert created["rule_type"] == "DIRECT_VERSION" and created["release_version_id"] == release_id
    assert created["target_reference"]["direct_version"]["version_raw"] == "v1"
    assert created["target_reference"]["family"]["family_id"] == 1
    assert any(row["source_type"] == "MANUAL" for row in all_rows(storage, "import_batch"))
    source = load_dashboard_source(storage)
    assert source.risks[snapshot.equipment_id] is RiskStatus.RISK


def test_rule_with_release_of_another_family_is_rejected_without_saving(revision_storage):  # noqa: F811
    storage = revision_storage
    other = release(storage, raw="c1", order=1, family=2)
    before = all_rows(storage, "risk_rule")

    with pytest.raises(ValueError, match="같은 계열"):
        ManagementService(storage).create_risk_rule(direct_rule(other))
    with pytest.raises(ValueError, match="쓸 수 없는 항목"):
        ManagementService(storage).create_risk_rule({**direct_rule(other), "risk_rule_id": 9})
    assert all_rows(storage, "risk_rule") == before


@pytest.fixture
def app():
    yield QApplication.instance() or QApplication([])


def wait(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not condition():
        app.processEvents()
    assert condition()


def test_management_page_registers_rule_through_dialog_and_emits_saved(app, revision_storage, file_storage):  # noqa: F811
    storage = file_storage  # 픽스처가 만든 파일 DB에 직접 등록한다
    release_id = release(storage, raw="v1", order=1)
    page = ManagementPage(ManagementGateway(storage), context_provider=lambda: RefreshContext())
    page.refresh()
    wait(app, lambda: not page.busy and bool(page.references))
    saved = []
    page.saved.connect(saved.append)

    releases, _ = risk_rule_options(page.references, 1)
    assert [key for key, _ in releases] == [release_id]

    class Accepting(RiskRuleDialog):
        def exec(self):
            self.reason.setPlainText("화면에서 등록한 위험 버전")
            self.verified.setChecked(True)
            return True

    page.family_choice.setCurrentIndex(page.family_choice.findData(1))
    assert page.open_risk_dialog(dialog_factory=Accepting)
    wait(app, lambda: not page.busy and saved)
    assert saved[0].records[0].current["risk_reason"] == "화면에서 등록한 위험 버전"
    assert len(all_rows(storage, "risk_rule")) == 1
