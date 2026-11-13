"""DB의 테이블 정의로 입력값을 검사하고 전달받은 세션으로 조회·저장한다."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, LargeBinary, MetaData, String, and_, select

from .validation import BINARY_COLUMNS, TABLE_NAMES, StorageValidationError, normalize_item_jira


class StorageRepository:
    """관리 테이블 레포지토리."""

    def __init__(self, connection):
        """현재 DB의 공통 관리 테이블 정의를 읽어 메타데이터를 구성한다.

        param
        - connection: 바깥쪽 트랜잭션에서 확보한 SQLAlchemy Connection.
        """
        self.metadata = MetaData()
        self.metadata.reflect(bind=connection, only=TABLE_NAMES)

    def table(self, name):
        """허용된 관리 테이블의 SQLAlchemy 정의를 조회한다.

        param
        - name: TABLE_NAMES에 등록된 관리 테이블 이름.

        return
        - DB에서 읽어 둔 Table 객체.

        raises
        - StorageValidationError: 지원하지 않는 테이블 이름인 경우.
        """
        if name not in TABLE_NAMES:
            raise StorageValidationError("지원하지 않는 관리 테이블입니다.")
        return self.metadata.tables[name]

    def key_filter(self, table, key):
        """전체 기본키로 조회·변경 대상 행의 검색 조건을 생성한다.

        문자열 원문 키는 UTF-8 바이트로 변환하고 정수 키의 숫자 문자열은 정수로 변환한다.
        실수·불리언·숫자 뒤에 문자가 붙은 값은 정수 ID로 허용하지 않는다.

        param
        - table: 기본키 정의를 확인할 Table 객체.
        - key: 모든 기본키 컬럼 이름과 값의 대응.

        return
        - 기본키 조건을 AND로 결합한 SQLAlchemy 표현식.

        raises
        - StorageValidationError: 기본키가 누락되거나 None·잘못된 정수 형식이 포함된 경우.
        """
        if set(key) != set(table.primary_key.columns.keys()):
            raise StorageValidationError("행을 식별하는 기본키 전체가 필요합니다.")
        clauses = []
        for field, value in key.items():
            if field in BINARY_COLUMNS.get(table.name, {}) and isinstance(value, str):
                value = value.encode("utf-8")
            if value is None:
                raise StorageValidationError("기본키는 비워 둘 수 없습니다.")
            if isinstance(table.c[field].type, Integer):
                # MySQL이 잘못된 문자열이나 실수를 다른 정수 ID로 해석하지 않게 한다.
                if isinstance(value, str) and value.isascii() and value.isdecimal():
                    try:
                        value = int(value)
                    except ValueError:
                        raise StorageValidationError("기본키를 정수로 변환할 수 없습니다.") from None
                if not isinstance(value, int) or isinstance(value, bool):
                    raise StorageValidationError("기본키는 정수 또는 숫자로만 된 문자열이어야 합니다.")
            clauses.append(table.c[field] == value)
        return and_(*clauses)

    def validate_columns(self, table, values, *, inserting=False):
        """DB 컬럼 정의에 맞춰 필수값·자료형·저장 범위·길이를 검사한다.

        모델·계열 사이의 관계나 편집 허용 필드 등 업무 규칙은 각 업무 서비스에서 검사한다.

        param
        - table: 컬럼과 기본값 정의를 확인할 Table 객체.
        - values: JSON 문자열·바이너리 원문 등 DB 저장 형식으로 변환한 값.
        - inserting: True이면 기본값·자동 생성 키를 제외한 필수 컬럼의 누락도 검사.

        raises
        - StorageValidationError: 없는 필드, 필수값 누락, 자료형·범위·길이 오류가 있는 경우.
        """
        if set(values) - set(table.c.keys()):
            raise StorageValidationError("테이블에 없는 필드가 포함됐습니다.")
        for column in table.c:
            if column.name not in values:
                auto_id = column.primary_key and len(table.primary_key) == 1 and isinstance(column.type, Integer)
                if inserting and not column.nullable and column.server_default is None and not auto_id:
                    raise StorageValidationError(f"{table.name}.{column.name}: 필수값이 없습니다.")
                continue
            value = values[column.name]
            if value is None:
                if not column.nullable:
                    raise StorageValidationError(f"{table.name}.{column.name}: 필수값을 비울 수 없습니다.")
                continue
            valid = True
            if isinstance(column.type, Integer):
                valid = isinstance(value, int) and (
                    not isinstance(value, bool) or column.name in {"analysis_enabled", "is_enabled"}
                )
                if valid and getattr(column.type, "unsigned", False):
                    valid = 0 <= value <= 4294967295
                elif valid:
                    valid = -2147483648 <= value <= 2147483647
            elif isinstance(column.type, LargeBinary):
                valid = isinstance(value, bytes) and len(value) <= column.type.length
            elif isinstance(column.type, String):
                valid = isinstance(value, str) and (column.type.length is None or len(value) <= column.type.length)
            elif isinstance(column.type, DateTime):
                valid = isinstance(value, datetime)
            elif isinstance(column.type, Date):
                valid = isinstance(value, date) and not isinstance(value, datetime)
            if not valid:
                raise StorageValidationError(f"{table.name}.{column.name}: 자료형 또는 길이가 올바르지 않습니다.")

    def get(self, session, name, key, *, for_update=False):
        """전체 기본키에 일치하는 관리 행을 조회한다.

        이 단계에서는 JSON 문자열과 바이너리를 복원하지 않는다.

        param
        - session: 현재 업무가 공유하는 SQLAlchemy Session.
        - name: 조회할 관리 테이블 이름.
        - key: 조회 대상의 전체 기본키.
        - for_update: True이면 조회한 행을 트랜잭션 종료까지 잠금.

        return
        - DB 저장 형식의 행 객체.

        raises
        - LookupError: 해당 기본키의 행이 없는 경우.
        - StorageValidationError: 테이블 또는 기본키 형식이 잘못된 경우.
        """
        table = self.table(name)
        query = select(table).where(self.key_filter(table, key))
        if for_update:
            query = query.with_for_update()
        row = session.execute(query).mappings().one_or_none()
        if row is None:
            raise LookupError(f"{name}: 해당 기록이 없습니다.")
        return dict(row)

    def insert(self, session, name, values):
        """컬럼 검증을 통과한 새 행을 저장하고 생성된 기본키를 조회한다.

        저장 확정은 바깥쪽 업무 트랜잭션에서 관리한다.

        param
        - session: 현재 업무가 공유하는 SQLAlchemy Session.
        - name: 등록할 관리 테이블 이름.
        - values: DB 저장 형식으로 변환한 등록값.

        return
        - 기본키 컬럼 이름과 생성된 값의 대응.

        raises
        - StorageValidationError: 테이블·필수값·자료형·길이가 잘못된 경우.
        """
        table = self.table(name)
        values = self._legacy_jira_values(table, values)
        self.validate_columns(table, values, inserting=True)
        result = session.execute(table.insert().values(**values))
        return dict(zip(table.primary_key.columns.keys(), result.inserted_primary_key))

    def update(self, session, name, key, values):
        """전체 기본키로 지정한 행의 관리값을 갱신한다.

        기본키 컬럼은 SET 대상에서 제외한다. 수정값이 없으면 UPDATE를 실행하지 않는다.
        대상 존재 확인과 행 잠금은 호출자가 처리하며 이 함수는 commit하지 않는다.

        param
        - session: 현재 업무가 공유하는 SQLAlchemy Session.
        - name: 수정할 관리 테이블 이름.
        - key: 수정 대상의 전체 기본키.
        - values: 컬럼 검증에 사용할 DB 저장 형식의 값.

        raises
        - StorageValidationError: 컬럼 값이나 기본키 형식이 잘못된 경우.
        """
        table = self.table(name)
        values = self._legacy_jira_values(table, values)
        self.validate_columns(table, values)
        changes = {field: value for field, value in values.items() if field not in table.primary_key.columns}
        if changes:
            session.execute(table.update().where(self.key_filter(table, key)).values(**changes))

    @staticmethod
    def _legacy_jira_values(table, values):
        """실제 열에 맞춰 이전 별칭만 변환한다. 저장할 수 없는 실제 값은 버리지 않는다."""
        if table.name != "item":
            return values
        result = normalize_item_jira(values)
        if "jira_code" not in result:
            return result
        code = result["jira_code"]
        if "jira_code" in table.c:
            if "jira_key" in table.c:
                result["jira_key"] = code
        elif "jira_key" in table.c:
            result["jira_key"] = result.pop("jira_code")
        elif code is None:
            result.pop("jira_code")
        else:
            raise StorageValidationError("item.jira_code: 이전 DB 구조에는 이슈 코드를 저장할 수 없습니다. DB 구조를 먼저 갱신해 주세요.")
        return result

    def delete(self, session, name, key):
        """전체 기본키로 지정한 행을 현재 세션에서 삭제한다.

        삭제 전 원문·위험 근거 보존은 호출자가 처리하며 이 함수는 commit하지 않는다.

        param
        - session: 현재 업무가 공유하는 SQLAlchemy Session.
        - name: 삭제할 관리 테이블 이름.
        - key: 삭제 대상의 전체 기본키.

        raises
        - StorageValidationError: 테이블 또는 기본키 형식이 잘못된 경우.
        """
        table = self.table(name)
        session.execute(table.delete().where(self.key_filter(table, key)))
