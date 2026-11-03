"""실제 관리 화면의 워커 저장에서 HPA 이력 충돌 보류·보완·재검증을 확인한다."""

from PySide6.QtTest import QSignalSpy

from setron.features.equipment.service import EquipmentService
from setron.ui.management_data import ManagementGateway
from setron.ui.management_page import ManagementPage

from test_hpa_management_safety import CODE, FIRST, LAST, seed_history, stored_rows
from test_management_app_integration import app, file_storage, wait_for  # noqa: F401
from test_release_revision import all_rows, revision_storage  # noqa: F401


def select(page, field, value):
    editor = page.inputs[field].editor
    index = editor.findData(value)
    assert index >= 0
    editor.setCurrentIndex(index)


def test_hpa_history_conflict_preserves_ui_draft_and_db_until_corrected(app, file_storage):
    storage = file_storage
    _, last = seed_history(storage)
    pm_id = last.software[0].pms[0].pm_id
    before = stored_rows(storage)
    originals = all_rows(storage, "import_batch")
    page = ManagementPage(ManagementGateway(storage))
    saved, failed = QSignalSpy(page.saved), QSignalSpy(page.save_failed)
    page.show()
    try:
        wait_for(app, lambda: not page.busy and page.record is not None)
        assert page.open_record("equipment_pm", {"pm_id": pm_id})
        wait_for(app, lambda: not page.busy and page.record is not None
                 and page.record.table == "equipment_pm" and page.record.key == {"pm_id": pm_id})
        select(page, "group_id", 1)
        select(page, "family_id", 1)
        page.inputs["model_name_raw"].editor.setText("M-O2")
        assert page.add_current() and page.review() and page.save()
        wait_for(app, lambda: not page.busy)

        assert page.state == "failed" and not page.locked
        assert failed.count() == 1 and saved.count() == 0
        assert CODE in page.notice.text() and FIRST.isoformat() in page.notice.text()
        assert LAST.isoformat() in page.notice.text()
        assert "HPA" in page.notice.text() and "일반 설비" in page.notice.text()
        assert "전체" in page.notice.text()
        assert page.pending and page.inputs["group_id"].value() == 1
        assert not page.save_button.isEnabled()
        assert stored_rows(storage) == before

        select(page, "group_id", 4)
        select(page, "family_id", 3)
        page.inputs["model_name_raw"].editor.setText("HPA")
        page.inputs["version_raw"].editor.setText("vHPA.CONFIRMED_d9 ")
        assert page.add_current() and page.review() and page.save()
        wait_for(app, lambda: not page.busy and not page.pending and page.record is not None)

        assert saved.count() == 1 and failed.count() == 1
        loaded = EquipmentService(storage).get_snapshot(CODE, LAST)
        assert loaded.software[0].version_raw == "vHPA.CONFIRMED_d9 "
        assert loaded.group_id == 4 and loaded.production_area is None
        assert EquipmentService(storage).get_snapshot(CODE, FIRST).software[0].version_raw == "vHPA.2_d5 "
        assert all_rows(storage, "import_batch")[:len(originals)] == originals
    finally:
        wait_for(app, lambda: not page.busy)
        page.close()
        page.deleteLater()
        app.processEvents()
