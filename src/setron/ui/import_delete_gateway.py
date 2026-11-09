"""기간별 삭제 화면(import_delete.ImportDeletePage)과 현황표 기준일 삭제 서비스(features.deletion) 연결.

설비 현황표만 지운다(멘토 요청 3번). 릴리즈노트는 설비 버전·N 그룹·위험 규칙이 참조하므로 조회만 한다.
"""

from __future__ import annotations

from setron.features.ai.schemas import ExtractKind
from setron.features.deletion import SnapshotDeletionService
from setron.ui.import_delete import DeletionBatch, DeletionCatalog, DeletionResult

_METHODS = {"EXCEL": "Excel 가져오기", "MANUAL": "직접 입력", "SEED": "시연 자료"}


class ImportDeleteGateway:
    """``list_batches()`` · ``delete(plan)``을 제공한다 (화면이 기대하는 모양)."""
    release_deletion_supported = False

    def __init__(self, service: SnapshotDeletionService, *, source_label: str = ""):
        self.service, self.source_label = service, source_label

    @classmethod
    def from_env(cls, environ=None) -> ImportDeleteGateway | None:
        service = SnapshotDeletionService.from_env(environ)
        return cls(service) if service is not None else None

    def list_batches(self) -> DeletionCatalog:
        revision, snapshots, releases = self.service.catalog()
        equipment_batches = tuple(DeletionBatch(
            key=b.key, kind=ExtractKind.EQUIPMENT_STATUS, day=b.base_date, site=b.site, family=None, sheet=b.sheet,
            equipment_count=b.equipment_count, row_count=b.row_count,
            saved_at=b.imported_at.strftime("%Y-%m-%d %H:%M") if b.imported_at else "",
            method=_METHODS.get(b.source_type.upper(), b.source_type or "")) for b in snapshots)
        release_batches = tuple(DeletionBatch(
            key=f"release:{b.batch_id}:{b.family_id}", kind=ExtractKind.RELEASE_NOTE, day=None, site=None,
            family=b.family, sheet=b.sheet, equipment_count=0, row_count=b.version_count,
            saved_at=b.imported_at.strftime("%Y-%m-%d %H:%M") if b.imported_at else "",
            method="DB 초기 자료" if b.source_type == "SEED" else _METHODS.get(b.source_type.upper(), b.source_type),
            deletable=False) for b in releases)
        return DeletionCatalog(revision, equipment_batches + release_batches, self.source_label)

    def delete(self, plan) -> DeletionResult:
        if plan.kind is not ExtractKind.EQUIPMENT_STATUS:
            return DeletionResult("failed", message="릴리즈노트 삭제는 아직 지원하지 않습니다. 설비 현황표만 지울 수 있습니다.")
        outcome = self.service.delete(plan.revision, plan.keys)
        return DeletionResult(outcome.status, tuple(outcome.deleted_keys), outcome.message, outcome.revision)
