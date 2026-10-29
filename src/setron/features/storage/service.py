"""입력 검증과 삭제 전 위험 근거 보존을 같은 저장 트랜잭션에서 처리한다."""

from contextlib import contextmanager
from copy import deepcopy

from sqlalchemy import or_, select

from .repository import StorageRepository
from .validation import StorageValidationError, decode_record, encode_record


class StorageService:
    def __init__(self, database):
        self.database = database
        # 초기 테이블 조회도 공통 접속·오류 처리와 연결 정리 절차 안에서 수행한다.
        with database.transaction() as session:
            self.repository = StorageRepository(session.connection())

    @contextmanager
    def transaction(self):
        """여러 조회·변경을 하나의 저장 업무로 묶는다."""
        with self.database.transaction() as session:
            yield StorageTransaction(self.repository, session)


class StorageTransaction:
    def __init__(self, repository, session):
        self.repository = repository
        self.session = session

    def get(self, table, key):
        return decode_record(table, self.repository.get(self.session, table, key))

    def insert(self, table, values):
        encoded = encode_record(table, values, inserting=True)
        key = self.repository.insert(self.session, table, encoded)
        return self.get(table, key)

    def update(self, table, key, changes):
        """대상 행을 잠근 뒤 변경값을 반영하고 전체 필드를 다시 검증한다."""
        current = decode_record(table, self.repository.get(self.session, table, key, for_update=True))
        primary_key = self.repository.table(table).primary_key.columns.keys()
        if any(field in changes and changes[field] != current[field] for field in primary_key):
            raise StorageValidationError("기록의 기본키는 변경할 수 없습니다.")
        current.update(deepcopy(changes))
        self.repository.update(self.session, table, key, encode_record(table, current))
        return self.get(table, key)

    def delete_release_item(self, item_id):
        """릴리즈 교체 내부 작업에서 Item을 삭제하고 관련 위험 근거를 보존한다."""
        self._delete_with_risk_reference(
            "item", {"item_id": item_id}, {"item_id": "item"},
            ("item_id", "item_no", "release_note_id", "content", "batch_id", "source_reference"),
        )

    def delete_release_version(self, version_id):
        """릴리즈 버전 삭제를 위험 근거 보존 절차와 함께 처리한다."""
        self._delete_with_risk_reference(
            "release_version", {"release_version_id": version_id},
            {"release_version_id": "direct_version", "start_release_version_id": "start_version", "end_release_version_id": "end_version"},
            ("release_version_id", "family_id", "version_raw", "version_suffix"),
        )

    def _delete_with_risk_reference(self, table_name, key, references, snapshot_fields):
        # 대상 행과 위험 규칙을 잠근 상태에서 근거 기록과 삭제를 같은 세션으로 처리한다.
        parent = decode_record(
            table_name, self.repository.get(self.session, table_name, key, for_update=True)
        )
        # 입력 ID가 문자열이어도 DB에서 읽은 정수 기본키로 참조 비교와 삭제를 수행한다.
        parent_key = {field: parent[field] for field in key}
        parent_id = next(iter(parent_key.values()))
        risk_table = self.repository.table("risk_rule")
        query = select(risk_table).where(or_(*(risk_table.c[field] == parent_id for field in references))).with_for_update()
        for row in self.session.execute(query).mappings().all():
            risk = decode_record("risk_rule", dict(row))
            # 사용자가 남긴 기존 근거에 삭제 대상의 원문 정보를 추가한다.
            saved = deepcopy(risk["target_reference"])
            for field, reference_name in references.items():
                if risk[field] == parent_id:
                    saved[reference_name] = {name: deepcopy(parent.get(name)) for name in snapshot_fields}
            self.update("risk_rule", {"risk_rule_id": risk["risk_rule_id"]}, {
                "target_reference": saved, "mapping_status": "UNRESOLVED",
            })
        self.repository.delete(self.session, table_name, parent_key)
