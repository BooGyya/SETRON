"""입력 검증과 삭제 전 위험 근거 보존을 같은 저장 트랜잭션에서 처리한다."""

from contextlib import contextmanager
from copy import deepcopy

from sqlalchemy import or_, select

from .repository import StorageRepository
from .validation import StorageValidationError, decode_record, encode_record


class StorageService:
    """공통 관리 데이터 저장 서비스."""

    def __init__(self, database):
        """공통 트랜잭션으로 테이블 정의를 읽고 저장 레포지토리를 준비한다.

        param
        - database: DB 연결과 업무별 트랜잭션을 관리하는 Database.

        raises
        - DatabaseConnectionError: 테이블 정의를 읽기 전에 DB 접속에 실패한 경우.
        - DatabaseSaveError: 연결 후 테이블 정의 조회에서 SQLAlchemy 오류가 발생한 경우.
        """
        self.database = database
        # 초기 테이블 조회도 공통 접속·오류 처리와 연결 정리 절차 안에서 수행한다.
        with database.transaction() as session:
            self.repository = StorageRepository(session.connection())

    @contextmanager
    def transaction(self):
        """여러 관리 테이블의 조회·변경을 하나의 저장 업무로 묶는다.

        블록 안에서 같은 StorageTransaction을 사용한다. 최종 저장 결과가 불명확한 오류는
        Database의 오류 구분을 그대로 전달하며 자동으로 재시도하지 않는다.

        return
        - with문 안에 StorageTransaction을 전달하는 컨텍스트 관리자.

        raises
        - DatabaseConnectionError: 업무 실행 전 DB 연결에 실패한 경우.
        - DatabaseSaveError: 조회·저장 또는 최종 commit에 SQLAlchemy 오류가 발생한 경우.
        - DatabaseCommitUnknownError: 최종 commit 중 연결 중단으로 반영 여부를 확인할 수 없는 경우.
        """
        with self.database.transaction() as session:
            yield StorageTransaction(self.repository, session)


class StorageTransaction:
    """관리 데이터 트랜잭션."""

    def __init__(self, repository, session):
        """한 업무에서 공유할 레포지토리와 세션을 연결한다.

        param
        - repository: 공통 테이블 정의와 DB 조회·저장을 제공하는 StorageRepository.
        - session: 바깥쪽 트랜잭션이 관리하는 SQLAlchemy Session.
        """
        self.repository = repository
        self.session = session

    def get(self, table, key):
        """기본키에 일치하는 행을 읽고 JSON·UTF-8 원문을 복원한다.

        param
        - table: 조회할 관리 테이블 이름.
        - key: 조회 대상의 전체 기본키.

        return
        - 앱에서 사용할 수 있는 문자열·객체로 복원한 행.

        raises
        - LookupError: 해당 기본키의 행이 없는 경우.
        - StorageValidationError: 테이블·기본키가 잘못됐거나 저장된 JSON을 읽을 수 없는 경우.
        """
        return decode_record(table, self.repository.get(self.session, table, key))

    def insert(self, table, values):
        """입력값을 검증·변환해 새 행을 저장하고 생성 결과를 다시 조회한다.

        param
        - table: 등록할 관리 테이블 이름.
        - values: JSON 객체와 문자열 등 앱 형식으로 전달한 등록값.

        return
        - 생성된 기본키와 DB 기본값을 포함한 복원된 행.

        raises
        - StorageValidationError: 필수값·자료형·상태·JSON 형식이 잘못된 경우.
        """
        encoded = encode_record(table, values, inserting=True)
        key = self.repository.insert(self.session, table, encoded)
        return self.get(table, key)

    def update(self, table, key, changes):
        """대상 행을 잠그고 기존값과 변경값을 합쳐 검증한 뒤 저장한다.

        이 공통 함수는 기본키 변경을 차단한다. 원본 불변과 편집 허용 필드는
        이 함수를 호출하는 설비·릴리즈 등 각 업무 서비스에서 검사한다.

        param
        - table: 수정할 관리 테이블 이름.
        - key: 수정 대상의 전체 기본키.
        - changes: 변경할 필드 이름과 앱 형식의 값.

        return
        - 변경 후 다시 조회한 복원된 행.

        raises
        - LookupError: 수정할 행이 없는 경우.
        - StorageValidationError: 기본키를 변경하거나 합친 입력값이 검증을 통과하지 못한 경우.
        """
        current = decode_record(table, self.repository.get(self.session, table, key, for_update=True))
        primary_key = self.repository.table(table).primary_key.columns.keys()
        if any(field in changes and changes[field] != current[field] for field in primary_key):
            raise StorageValidationError("기록의 기본키는 변경할 수 없습니다.")
        current.update(deepcopy(changes))
        self.repository.update(self.session, table, key, encode_record(table, current))
        return self.get(table, key)

    def delete_release_item(self, item_id):
        """릴리즈 교체 내부에서 Item을 삭제하고 기존 위험 지정의 원본 근거를 보존한다.

        일반 Item 직접 삭제용 API가 아니며 릴리즈 교체 절차에서만 사용한다.

        param
        - item_id: 삭제할 Item의 정수 ID 또는 ASCII 숫자로만 된 ID 문자열.

        raises
        - LookupError: 삭제할 Item이 없는 경우.
        - StorageValidationError: ID 형식이나 위험 근거의 JSON 형식이 잘못된 경우.
        """
        self._delete_with_risk_reference(
            "item", {"item_id": item_id}, {"item_id": "item"},
            ("item_id", "item_no", "release_note_id", "content", "batch_id", "source_reference"),
        )

    def delete_release_version(self, version_id):
        """릴리즈 버전을 삭제하고 직접·시작·해제 버전의 위험 근거를 보존한다.

        param
        - version_id: 삭제할 릴리즈의 정수 ID 또는 ASCII 숫자로만 된 ID 문자열.

        raises
        - LookupError: 삭제할 릴리즈가 없는 경우.
        - StorageValidationError: ID 형식이나 위험 근거의 JSON 형식이 잘못된 경우.
        """
        self._delete_with_risk_reference(
            "release_version", {"release_version_id": version_id},
            {"release_version_id": "direct_version", "start_release_version_id": "start_version", "end_release_version_id": "end_version"},
            ("release_version_id", "family_id", "version_raw", "version_suffix"),
        )

    def _delete_with_risk_reference(self, table_name, key, references, snapshot_fields):
        """삭제 대상과 관련 위험 규칙을 잠그고 근거 보존·삭제를 같은 트랜잭션에서 처리한다.

        기존 위험 근거에 삭제할 행의 원문을 추가하고 연결 상태를 UNRESOLVED로 변경한다.
        이후 삭제가 실패하거나 업무가 취소되면 근거 변경도 함께 취소된다.

        param
        - table_name: 삭제 대상 테이블. item 또는 release_version.
        - key: 삭제 대상의 전체 기본키.
        - references: 위험 규칙의 참조 컬럼 이름과 근거 저장 항목 이름의 대응.
        - snapshot_fields: 삭제 전에 근거로 복사할 대상 행의 필드 이름 목록.

        raises
        - LookupError: 삭제할 대상 행이 없는 경우.
        - StorageValidationError: 기본키나 위험 근거 값이 잘못된 경우.
        """
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
