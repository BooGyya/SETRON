"""설비 현황표 기준일 삭제 (멘토 5차 요청 3번: "1~5월을 넣은 뒤 2월만 지울 수 있게").

삭제 단위는 '저장 묶음 하나의 한 기준일·한 사업장'이다. 키는 ``status:<묶음 ID>:<기준일>:<사업장>``이며 바뀌지 않는다.
한 번에 고른 단위는 한 트랜잭션에서 PM → SW 버전 → 설비 현황 순서로 모두 지우거나 하나도 지우지 않는다.

- 목록을 볼 때의 revision(전체 현황 지문)과 삭제 직전 revision이 다르면(그사이 저장·삭제가 있었으면) 지우지 않는다.
- 설비 호기(equipment)와 저장 묶음 기록(import_batch, 원본 셀)은 남긴다. 다른 기준일 현황이 쓰고 있을 수 있고,
  가져오기 이력은 감사 기록이기 때문이다. 릴리즈노트·위험 규칙·N 그룹은 건드리지 않는다.
- 최종 commit 결과를 알 수 없으면 'unknown'으로 돌려준다(다시 시도하지 말고 목록을 새로 읽어 확인).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from threading import Lock

from sqlalchemy import func, select

from setron.features.storage.service import StorageService
from setron.infrastructure.database import Database, DatabaseCommitUnknownError


@dataclass(frozen=True)
class SnapshotBatch:
    """지울 수 있는 단위: 저장 묶음 하나의 한 기준일·한 사업장."""

    key: str
    batch_id: int
    base_date: date
    site: str
    sheet: str
    equipment_count: int          # 설비 대수
    row_count: int                # 지워질 SW 버전 행 수 (설비 × 계열)
    imported_at: datetime | None
    source_type: str              # EXCEL · MANUAL 등


@dataclass(frozen=True)
class DeleteOutcome:
    """삭제 결과. status: success(요청한 단위 전부 삭제) · failed(하나도 안 지움) · unknown(commit 결과 모름)."""

    status: str
    deleted_keys: tuple[str, ...] = ()
    message: str = ""
    revision: str = ""


def batch_key(batch_id: int, base_date: date, site: str) -> str:
    return f"status:{batch_id}:{base_date.isoformat()}:{site}"


def _sheet(raw) -> str:
    """import_batch.raw_data({시트: {셀: 값}})의 시트 이름. 없으면 '시트 미확인'."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return "시트 미확인"
    if isinstance(raw, Mapping) and raw:
        return ", ".join(str(name) for name in raw)
    return "시트 미확인"


class SnapshotDeletionService:
    """현황표 기준일 삭제 서비스. DB 연결은 첫 사용 때 한다 (앱 시작만으로 DB에 붙지 않는다).

    param
    - storage: ``StorageService``. 없으면 ``connect``로 처음 쓸 때 만든다.
    """

    def __init__(self, storage: StorageService | None = None, *, connect=None):
        self._storage, self._connect, self._lock = storage, connect, Lock()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SnapshotDeletionService | None:
        """``SETRON_DB_*`` 접속 설정이 있으면 서비스를, 없으면 None(삭제 화면 미연결)을 돌려준다."""
        values = os.environ if environ is None else environ
        if not values.get("SETRON_DB_NAME"):
            return None
        return cls(connect=lambda: StorageService(Database.from_env(values)))

    @property
    def storage(self) -> StorageService:
        if self._storage is None:
            with self._lock:
                if self._storage is None:
                    self._storage = self._connect()
        return self._storage

    # --- 목록 ---------------------------------------------------------------------------
    def batches(self) -> tuple[str, tuple[SnapshotBatch, ...]]:
        """(revision, 지울 수 있는 단위 목록). 최신 기준일부터."""
        with self.storage.transaction() as tx:
            return self._read(tx)

    def _read(self, tx, *, lock: bool = False):
        table = tx.repository.table
        snapshot, version, batch = table("equipment_snapshot"), table("equipment_version"), table("import_batch")
        query = select(snapshot.c.snapshot_id, snapshot.c.equipment_id, snapshot.c.base_date, snapshot.c.site,
                       snapshot.c.batch_id)
        if lock:
            query = query.with_for_update()
        rows = tx.session.execute(query).all()
        counts = dict(tx.session.execute(
            select(version.c.snapshot_id, func.count()).group_by(version.c.snapshot_id)).all())
        groups: dict[tuple, list] = defaultdict(list)
        for row in rows:
            base = row.base_date if isinstance(row.base_date, date) else date.fromisoformat(str(row.base_date))
            groups[(row.batch_id, base, row.site)].append(row)
        batch_ids = sorted({key[0] for key in groups})
        info = {}
        if batch_ids:
            for row in tx.session.execute(select(batch.c.batch_id, batch.c.source_type, batch.c.imported_at,
                                                 batch.c.raw_data).where(batch.c.batch_id.in_(batch_ids))).all():
                info[row.batch_id] = row
        found = []
        for (batch_id, base, site), members in groups.items():
            meta = info.get(batch_id)
            found.append(SnapshotBatch(
                key=batch_key(batch_id, base, site), batch_id=batch_id, base_date=base, site=site,
                sheet=_sheet(meta.raw_data) if meta is not None else "시트 미확인",
                equipment_count=len({r.equipment_id for r in members}),
                row_count=sum(counts.get(r.snapshot_id, 0) for r in members),
                imported_at=getattr(meta, "imported_at", None), source_type=getattr(meta, "source_type", "") or ""))
        found.sort(key=lambda b: (b.base_date, b.site, b.batch_id), reverse=True)
        fingerprint = hashlib.sha256(json.dumps(
            [(b.key, b.equipment_count, b.row_count) for b in found] + [max((r.snapshot_id for r in rows), default=0)],
            ensure_ascii=False).encode()).hexdigest()[:16]
        return fingerprint, tuple(found)

    # --- 삭제 ---------------------------------------------------------------------------
    def delete(self, revision: str, keys: Sequence[str]) -> DeleteOutcome:
        """고른 단위를 모두 지운다. 목록이 바뀌었거나 없는 키가 있으면 하나도 지우지 않는다."""
        keys = tuple(dict.fromkeys(keys))
        if not keys:
            return DeleteOutcome("failed", message="지울 기준일을 골라 주세요.")
        try:
            with self.storage.transaction() as tx:
                current, batches = self._read(tx, lock=True)
                if current != revision:
                    return DeleteOutcome("failed", message="목록을 연 뒤 저장·삭제가 있었습니다. 목록을 다시 불러온 뒤 골라 주세요.",
                                         revision=current)
                by_key = {b.key: b for b in batches}
                missing = [key for key in keys if key not in by_key]
                if missing:
                    return DeleteOutcome("failed", message=f"이미 없거나 지울 수 없는 항목이 있습니다: {', '.join(missing)}",
                                         revision=current)
                table = tx.repository.table
                snapshot, version, pm = table("equipment_snapshot"), table("equipment_version"), table("equipment_pm")
                ids = []
                for key in keys:
                    target = by_key[key]
                    ids += [row[0] for row in tx.session.execute(select(snapshot.c.snapshot_id).where(
                        snapshot.c.batch_id == target.batch_id, snapshot.c.base_date == target.base_date,
                        snapshot.c.site == target.site)).all()]
                for start in range(0, len(ids), 500):              # IN 목록이 너무 길지 않게 나눠 지운다
                    chunk = ids[start:start + 500]
                    tx.session.execute(pm.delete().where(pm.c.snapshot_id.in_(chunk)))
                    tx.session.execute(version.delete().where(version.c.snapshot_id.in_(chunk)))
                    tx.session.execute(snapshot.delete().where(snapshot.c.snapshot_id.in_(chunk)))
                after, _ = self._read(tx)
            removed = sum(by_key[k].equipment_count for k in keys)
            return DeleteOutcome("success", keys, f"기준일 {len(keys)}개 · 설비 현황 {removed}건을 지웠습니다.", after)
        except DatabaseCommitUnknownError:
            return DeleteOutcome("unknown", message="삭제 결과를 확인하지 못했습니다(연결 끊김). 다시 누르지 말고 목록을 새로 불러와 확인해 주세요.")
        except Exception as error:  # noqa: BLE001 — 화면에 원인을 보여 주고 아무것도 지우지 않은 것으로 처리
            return DeleteOutcome("failed", message=f"삭제하지 못했습니다: {error}")
