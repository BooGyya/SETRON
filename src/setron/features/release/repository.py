"""릴리즈·Item 테이블 조회 및 원본 셀 복원."""

from copy import deepcopy

from sqlalchemy import select

from setron.features.storage.inputs import merge_sources, require
from setron.features.storage.validation import decode_record

from .schemas import RecordView, SourceCell


class ReleaseRepository:
    """릴리즈·Item 테이블 레포지토리."""

    def get(self, tx, table, key, *, lock=False):
        """기본키에 일치하는 관리 행을 조회한다.

        param
        - tx: 현재 업무에서 사용하는 공통 저장 트랜잭션.
        - table: 조회할 관리 테이블 이름.
        - key: 조회 대상의 전체 기본키.
        - lock: True이면 조회한 행을 트랜잭션이 끝날 때까지 잠금.

        return
        - JSON과 UTF-8 바이트를 복원한 관리 행.

        raises
        - LookupError: 해당 기본키의 기록이 없는 경우.
        - StorageValidationError: 테이블·기본키 형식이 잘못됐거나 저장된 JSON을 읽을 수 없는 경우.
        """
        return decode_record(table, tx.repository.get(tx.session, table, key, for_update=lock))

    def groups(self, tx):
        """릴리즈가 저장된 계열의 관리 모델 목록을 조회한다.

        param
        - tx: 현재 조회에 사용하는 공통 저장 트랜잭션.

        return
        - group_id 순서의 관리 모델 행 목록. 릴리즈가 없으면 빈 튜플.
        """
        group = tx.repository.table("equipment_group")
        family = tx.repository.table("version_family")
        release = tx.repository.table("release_version")
        query = select(group).join(family, family.c.group_id == group.c.group_id).join(
            release, release.c.family_id == family.c.family_id,
        ).distinct().order_by(group.c.group_id)
        return tuple(dict(row) for row in tx.session.execute(query).mappings())

    def families(self, tx, group_id):
        """지정한 관리 모델에 릴리즈가 저장된 계열 목록을 조회한다.

        param
        - tx: 현재 조회에 사용하는 공통 저장 트랜잭션.
        - group_id: 계열 관리 모델 ID.

        return
        - family_id 순서의 계열 행 목록. 릴리즈가 없으면 빈 튜플.
        """
        family = tx.repository.table("version_family")
        release = tx.repository.table("release_version")
        query = select(family).join(release, release.c.family_id == family.c.family_id).where(
            family.c.group_id == group_id,
        ).distinct().order_by(family.c.family_id)
        return tuple(dict(row) for row in tx.session.execute(query).mappings())

    def releases(self, tx, family_id):
        """버전 계열에 등록된 릴리즈를 조회한다.

        version_order 오름차순으로 정렬한다. 같은 순서이거나 순서가 미확인인 행은
        ID로 표시 순서를 고정하며, 이 ID 순서를 릴리즈의 선후관계로 해석하지 않는다.

        param
        - tx: 현재 조회에 사용하는 공통 저장 트랜잭션.
        - family_id: 조회할 버전 계열 ID.

        return
        - 확인된 순서가 앞선 릴리즈 행 목록. 순서 미확인은 마지막에 배치.
        """
        table = tx.repository.table("release_version")
        query = select(table).where(table.c.family_id == family_id).order_by(
            table.c.version_order.is_(None), table.c.version_order, table.c.release_version_id,
        )
        return tuple(decode_record("release_version", dict(row)) for row in tx.session.execute(query).mappings())

    def links(self, tx, release_id):
        """릴리즈에 명시적으로 저장된 모든 Item 연결을 조회한다.

        param
        - tx: 현재 조회에 사용하는 공통 저장 트랜잭션.
        - release_id: 조회할 릴리즈 ID.

        return
        - item_id 순서의 연결 행 목록. 미포함·미확인 상태도 제외하지 않음.
        """
        table = tx.repository.table("release_version_item")
        query = select(table).where(table.c.release_version_id == release_id).order_by(table.c.item_id)
        return tuple(decode_record("release_version_item", dict(row)) for row in tx.session.execute(query).mappings())

    def view(self, tx, table, row):
        """현재 관리값과 입력 작업에 보관된 원본 셀 값을 분리해 구성한다.

        param
        - tx: 현재 조회에 사용하는 공통 저장 트랜잭션.
        - table: release_version 또는 item.
        - row: 원본과 비교할 현재 관리 행.

        return
        - 원본 위치와 값이 포함된 RecordView. 출처가 없으면 original_cells는 빈 튜플.

        raises
        - StorageValidationError: 출처 형식이 잘못됐거나 원본에 없는 셀을 참조하는 경우.
        - LookupError: 행에 연결된 입력 작업이 없는 경우.
        """
        batch = tx.get("import_batch", {"batch_id": row["batch_id"]})
        reference = merge_sources(row["source_reference"])
        raw = batch["raw_data"]
        cells = []
        for field, locations in (reference or {}).items():
            for location in locations if isinstance(locations, list) else [locations]:
                sheet = raw.get(location["sheet"]) if isinstance(raw, dict) else None
                require(isinstance(sheet, dict) and all(address in sheet for address in location["cells"]),
                        "원본 입력 작업에 없는 시트·셀을 참조하고 있습니다.")
                cells.extend(SourceCell(field=field, sheet=location["sheet"], address=address,
                    value=deepcopy(sheet[address])) for address in location["cells"])
        return RecordView(table=table, current=deepcopy(row), source_type=batch["source_type"],
                          imported_at=batch["imported_at"], original_cells=tuple(cells))
