"""공통 저장 트랜잭션 안에서 설비와 정확한 기준일의 현황을 조회한다."""

from collections import defaultdict
from dataclasses import fields

from sqlalchemy import select

from setron.features.storage.validation import StorageValidationError, decode_record

from .schemas import SnapshotInput, StoredPm, StoredSnapshot, StoredSoftware


class EquipmentRepository:
    """설비 테이블 레포지토리."""

    def rows(self, tx, name, *, lock=False, **filters):
        """검색 조건에 맞는 테이블 행을 조회하고 저장 형식을 파이썬 값으로 복원한다.

        param
        - tx: 현재 업무에서 사용하는 공통 저장 트랜잭션.
        - name: 조회할 관리 테이블 이름.
        - filters: 컬럼명과 값으로 지정하는 검색 조건. 여러 조건은 모두 만족해야 함.

        return
        - 기본키 순서로 정렬한 행 목록. 일치하는 행이 없으면 빈 목록.

        raises
        - StorageValidationError: 지원하지 않는 테이블이거나 저장된 JSON을 읽을 수 없는 경우.
        """
        table = tx.repository.table(name)
        query = select(table).filter_by(**filters).order_by(*table.primary_key.columns)
        if lock:
            query = query.with_for_update()
        return [decode_record(name, dict(row)) for row in tx.session.execute(query).mappings()]

    def find_equipment(self, tx, code, *, lock=False):
        """전체 사업장에서 호기 코드가 정확히 일치하는 설비를 조회한다.

        param
        - tx: 현재 업무에서 사용하는 공통 저장 트랜잭션.
        - code: 조회할 호기 코드. 대소문자와 앞뒤 공백까지 구분.
        - lock: True이면 조회한 설비 행을 저장 작업이 끝날 때까지 잠금. 기본값은 False.

        return
        - 조회한 설비 행. 일치하는 설비가 없으면 None.
        """
        table = tx.repository.table("equipment")
        query = select(table).where(table.c.equipment_code == code.encode("utf-8"))
        if lock:
            query = query.with_for_update()
        row = tx.session.execute(query).mappings().one_or_none()
        return decode_record("equipment", dict(row)) if row is not None else None

    def load(self, tx, equipment, snapshot, *, lock=False):
        """설비 현황에 적용 SW와 PM 기록을 연결해 조회 결과를 구성한다.

        param
        - tx: 현재 업무에서 사용하는 공통 저장 트랜잭션.
        - equipment: 조회 대상 설비의 DB 행.
        - snapshot: 조회 대상 기준일의 설비 현황 DB 행.

        return
        - 해당 현황의 SW와 PM을 포함한 StoredSnapshot.

        raises
        - StorageValidationError: PM에 연결된 SW 기록이 없거나 저장된 JSON을 읽을 수 없는 경우.
        """
        versions = self.rows(tx, "equipment_version", lock=lock, snapshot_id=snapshot["snapshot_id"])
        pms = self.rows(tx, "equipment_pm", lock=lock, snapshot_id=snapshot["snapshot_id"])
        return self._stored_snapshot(equipment, snapshot, versions, pms)

    def load_all(self, tx, equipment, *, lock=False):
        """같은 트랜잭션의 전체 현황·SW·PM을 세 번 읽어 기본키 순서대로 연결한다.

        현황마다 SW와 PM을 따로 조회하지 않는다. 원문과 날짜별 위치, PM의 SW 연결 검증은
        단건 :meth:`load`와 같으며, 조회한 행을 메모리에서 연결할 때만 묶는다.
        """
        snapshots = self.rows(tx, "equipment_snapshot", lock=lock)
        if not snapshots:
            return ()
        versions, pms = defaultdict(list), defaultdict(list)
        for version in self.rows(tx, "equipment_version", lock=lock):
            versions[version["snapshot_id"]].append(version)
        for pm in self.rows(tx, "equipment_pm", lock=lock):
            pms[pm["snapshot_id"]].append(pm)
        return tuple(self._stored_snapshot(equipment[snapshot["equipment_id"]], snapshot,
                                           versions[snapshot["snapshot_id"]], pms[snapshot["snapshot_id"]])
                     for snapshot in snapshots)

    @staticmethod
    def _stored_snapshot(equipment, snapshot, versions, pms):
        # NULL 연결의 PM을 조용히 버려 정상 현황처럼 반환하지 않는다.
        version_ids = {version["equipment_version_id"] for version in versions}
        if any(pm["equipment_version_id"] not in version_ids for pm in pms):
            raise StorageValidationError("PM의 SW 연결을 확인해야 합니다.")
        pms_by_version = defaultdict(list)
        for pm in pms:
            pms_by_version[pm["equipment_version_id"]].append(pm)
        software = tuple(StoredSoftware(
            **{key: version[key] for key in (
                "equipment_version_id", "family_id", "version_raw", "release_version_id", "batch_id", "source_reference",
            )},
            pms=tuple(StoredPm(**{key: pm[key] for key in (
                "pm_id", "pm_name", "group_id", "model_name_raw", "batch_id", "source_reference",
            )}) for pm in pms_by_version[version["equipment_version_id"]]),
        ) for version in versions)
        return StoredSnapshot(
            **{field.name: snapshot.get(field.name) for field in fields(SnapshotInput)
               if field.name not in {"equipment_code", "software"}},
            equipment_code=equipment["equipment_code"], equipment_id=equipment["equipment_id"],
            snapshot_id=snapshot["snapshot_id"], software=software,
        )
