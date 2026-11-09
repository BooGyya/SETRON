"""기간별 삭제 화면 ↔ 현황표 기준일 삭제 서비스 연결 (멘토 5차 요청 3번). DB 없이 연결부와 화면 흐름을 확인한다."""

from datetime import date, datetime
from time import monotonic

from PySide6.QtTest import QTest

from setron.features.ai.schemas import ExtractKind
from setron.features.deletion import DeleteOutcome, SnapshotBatch
from setron.ui.import_delete import DeletionPlan, ImportDeletePage
from setron.ui.import_delete_gateway import ImportDeleteGateway
from test_ui_import_ver5 import approve, own  # noqa: F401


class FakeService:
    def __init__(self):
        self.calls = []
        self.batches_ = tuple(SnapshotBatch(f"status:{i}:{d.isoformat()}:화성", i, d, "화성", "Memory", 85, 85,
                                            datetime(2026, d.month, 2, 9, 30), "EXCEL")
                              for i, d in enumerate((date(2026, 3, 1), date(2026, 2, 1), date(2026, 1, 1)), 1))

    def catalog(self):
        return "rev-1", self.batches_, ()

    def delete(self, revision, keys):
        self.calls.append((revision, tuple(keys)))
        return DeleteOutcome("success", tuple(keys), "기준일 1개 · 설비 현황 85건을 지웠습니다.", "rev-2")


def test_catalog_is_mapped_to_delete_screen_format():
    catalog = ImportDeleteGateway(FakeService()).list_batches()
    assert catalog.revision == "rev-1" and catalog.source_label == ""          # 위쪽 출처 안내 문장은 두지 않는다
    first = catalog.batches[0]
    assert (first.kind, first.day, first.site, first.sheet, first.equipment_count) == (
        ExtractKind.EQUIPMENT_STATUS, date(2026, 3, 1), "화성", "Memory", 85)
    assert first.saved_at == "2026-03-02 09:30" and first.method == "Excel 가져오기"


def test_release_note_deletion_is_refused_without_calling_service():
    service = FakeService()
    plan = DeletionPlan("rev-1", ("release:1",), ExtractKind.RELEASE_NOTE, None, None, None, "C3")
    result = ImportDeleteGateway(service).delete(plan)
    assert result.status == "failed" and "설비 현황표만" in result.message and not service.calls


def test_delete_screen_removes_only_february(own):
    service = FakeService()
    page = own(ImportDeletePage(ImportDeleteGateway(service)))
    page.set_catalog(page.gateway.list_batches())
    page.site_combo.setCurrentIndex(page.site_combo.findData("화성"))
    page.start.setText("2026-02-01")
    page.end.setText("2026-02-28")
    page.open_dialog = approve
    assert page.confirm_delete()
    end = monotonic() + 5
    while page.request.busy and monotonic() < end:
        QTest.qWait(10)
    assert service.calls == [("rev-1", ("status:2:2026-02-01:화성",))]
    assert [b.day for b in page.catalog.batches] == [date(2026, 3, 1), date(2026, 1, 1)]


def test_gateway_is_not_created_without_db_settings():
    assert ImportDeleteGateway.from_env({}) is None
