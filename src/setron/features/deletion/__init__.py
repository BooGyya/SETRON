"""저장한 설비 현황표를 기준일 단위로 지운다 (멘토 5차 요청 3번)."""

from .service import DeleteOutcome, SnapshotBatch, SnapshotDeletionService

__all__ = ["DeleteOutcome", "SnapshotBatch", "SnapshotDeletionService"]
