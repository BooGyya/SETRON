"""실제 위험 기준 저장 흐름의 잠금이 조회 조건 확정 전에 적용되는지 검증한다."""

from dataclasses import replace
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, Qt
from PySide6.QtTest import QTest

from setron.features.management import ManagementResult
from setron.infrastructure.database import DatabaseCommitUnknownError
from setron.ui.app import create_window
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.risk_rule_save import RiskRuleSaveController
from test_ui_dashboard import app  # noqa: F401


def wait_until(predicate):
    deadline = monotonic() + 10
    while not predicate() and monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


class SaveGateway:
    def __init__(self, *, unknown):
        self.unknown = unknown
        self.entered, self.release = Event(), Event()

    def create_risk_rule(self, values, context):
        self.entered.set()
        assert self.release.wait(20), "합성 저장 완료 신호가 도착하지 않았습니다."
        if self.unknown:
            raise DatabaseCommitUnknownError("합성 저장 결과 미확인")
        return ManagementResult((), None, (), None, 1)


@pytest.fixture
def scenario(app):
    windows = []

    def create(*, unknown):
        source = demo_dashboard_source()
        reads, original = [], source.snapshot

        def snapshot(query):
            reads.append(query)
            return original(query)

        source.snapshot = snapshot
        gateway = SaveGateway(unknown=unknown)
        window = create_window(source, management_gateway=gateway)
        windows.append((window, gateway))
        window.resize(960, 600)
        window.show()
        window.query_panel.set_as_of(max(source.analysis_dates))
        window.query_panel.apply_draft()
        wait_until(lambda: window.pages["dashboard"].snapshot is not None)
        controller = window.findChild(RiskRuleSaveController)
        assert controller is not None
        return SimpleNamespace(window=window, source=source, reads=reads, gateway=gateway, controller=controller)

    yield create
    for _, gateway in windows:
        gateway.release.set()
    assert QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()
    for window, _ in windows:
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_saving_and_unknown_commit_block_query_without_mutating_applied_state(scenario, monkeypatch):
    case = scenario(unknown=True)
    window, panel = case.window, case.window.query_panel
    previous, previous_range = window.query_state.applied, panel._applied_range
    assert case.controller.create_risk_rule({"reason": "합성 위험 기준"})
    wait_until(case.gateway.entered.is_set)
    assert window.management_saving and window.pages["dashboard"].snapshot is None

    panel.select("site_key", "giheung")
    assert panel.apply_draft() is None
    assert window.query_state.applied is previous and panel._applied_range == previous_range
    assert len(case.reads) == 1 and not window.query_request.busy
    assert panel.status.isVisibleTo(window) and "잠김" in panel.status.text()
    assert "저장" in panel.status.toolTip()

    case.gateway.release.set()
    wait_until(lambda: case.controller.locked and not case.controller.busy)
    assert not window.management_saving
    window.navigate("dashboard")
    assert panel.search_button.isVisibleTo(window)
    QTest.mouseClick(panel.search_button, Qt.MouseButton.LeftButton)
    assert panel.apply_draft() is None
    assert window.query_state.applied is previous and window.query_state.draft.site_key == "giheung"
    assert panel._applied_range == previous_range
    assert len(case.reads) == 1 and not window.query_request.busy
    assert panel.status.isVisibleTo(window) and "잠김" in panel.status.text()
    assert "저장" in panel.status.toolTip()
    assert previous.description in panel.applied_summary.text()

    # 저장 중 아직 열지 않았던 Replay도 독립 조회 상태를 확정하거나 워커를 시작하면 안 된다.
    replay = window.pages["replay"]
    starts, original = [], replay.read_request.start

    def start(loader):
        starts.append(loader)
        return original(loader)

    monkeypatch.setattr(replay.read_request, "start", start)
    window.navigate("replay")
    replay.query_panel.select("site_key", "giheung")
    QTest.mouseClick(replay.query_panel.search_button, Qt.MouseButton.LeftButton)
    assert replay.apply_query() is False
    assert replay.state.applied is None and replay.pending_request is None
    assert starts == [] and not replay.busy and len(case.reads) == 1
    assert replay.query_panel.status.isVisibleTo(window) and "잠김" in replay.query_panel.status.text()
    assert "저장" in replay.query_panel.status.toolTip()
    assert window.query_state.applied is previous


def test_successful_save_can_reapply_same_query_to_reloaded_catalog(scenario):
    case = scenario(unknown=False)
    window, panel = case.window, case.window.query_panel
    previous = window.query_state.applied
    updated = replace(case.source.catalog, source_label="합성 저장 뒤 갱신된 선택지")

    def reload_catalog():
        case.source.catalog = updated
        return updated

    case.source.reload_catalog = reload_catalog
    assert case.controller.create_risk_rule({"reason": "합성 위험 기준"})
    wait_until(case.gateway.entered.is_set)
    assert window.management_saving
    case.gateway.release.set()
    wait_until(lambda: not window.management_saving and window.pages["dashboard"].snapshot is not None)
    applied = window.query_state.applied
    assert not case.controller.locked and applied is not previous
    assert applied.selection == previous.selection and applied.targets == previous.targets
    assert window.query_state.catalog == updated and window.query_state.is_current(applied)
    assert window.pages["dashboard"].snapshot.query is applied
    assert case.reads == [previous, applied]
    assert not window.query_request.busy and not panel.status.isVisibleTo(window)
