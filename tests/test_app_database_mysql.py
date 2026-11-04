"""실제 DB의 자료 없음과 저장 뒤 최신 자료 조회를 앱 전체 연결로 검증한다."""

import os
from time import monotonic

import pytest
from PySide6.QtTest import QTest

from setron.features.ai.schemas import ExtractKind
from setron.features.import_save.service import DatabaseImportSaver
from setron.ui.app import create_window
from setron.ui.database_source import DatabaseDashboardSource, load_dashboard_source
from test_import_save_mysql import EQUIPMENT, request, row, storage  # noqa: F401
from test_schema_mysql import schema_database  # noqa: F401
from test_ui_import import app  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 설정 필요")


def wait_reads(window):
    end = monotonic() + 10
    while any(window.pages[key].read_request.busy for key in ("versions", "items")) and monotonic() < end:
        QTest.qWait(10)
    assert not any(window.pages[key].read_request.busy for key in ("versions", "items"))


def test_empty_database_then_saved_rows_use_live_data_without_mock_substitution(app, storage):
    source = DatabaseDashboardSource(lambda catalog=None: load_dashboard_source(storage, catalog))
    saver = DatabaseImportSaver(storage)
    window = create_window(source, import_saver=saver)
    try:
        assert not source.records
        for key in ("dashboard", "heatmap", "equipment"):
            assert "DB 자료" in window.pages[key].result_status.text()
            assert "예시" not in window.pages[key].result_status.text()
        window.query_panel.apply_draft()
        dashboard, heatmap = window.pages["dashboard"], window.pages["heatmap"]
        assert dashboard.snapshot is None and dashboard.no_observations
        assert "저장된 설비 현황 없음" in dashboard.result_status.text()
        assert dashboard.result_status.tone == "info"
        assert "저장된 설비 현황 없음" in heatmap.result_status.text()
        assert dashboard.metrics["total"].value.text() == "—"  # 관측일을 임의로 만들지 않는다.
        wait_reads(window)

        previous = None
        for index in (1, 2):
            record = row(1, {"equipment_code": "A1", "version_raw": "B1"}, **EQUIPMENT,
                         equipment_code=f"APP-DB-{index}", model_name="M-O2", family_name="O2", version_raw="v1")
            receipt = saver.save(request(ExtractKind.EQUIPMENT_STATUS, [record], f"app-db-{index}"))
            window.pages["import"].saved.emit(receipt)
            window.query_panel.apply_draft()
            snapshot = dashboard.snapshot
            assert snapshot is not None and snapshot is not previous
            assert snapshot.summary.equipment_count == index
            assert {record.equipment_code for record in snapshot.selected_records} == {
                f"APP-DB-{value}" for value in range(1, index + 1)}
            assert heatmap.snapshot is snapshot
            assert not dashboard.no_observations and "DB 자료" in dashboard.result_status.text()
            previous = snapshot
            wait_reads(window)
    finally:
        wait_reads(window)
        window.close()
        window.deleteLater()
        app.processEvents()
