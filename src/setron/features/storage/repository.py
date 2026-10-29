"""DB의 테이블 정의로 입력값을 검사하고 전달받은 세션으로 조회·저장한다."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, LargeBinary, MetaData, String, and_, select

from .validation import BINARY_COLUMNS, TABLE_NAMES, StorageValidationError


class StorageRepository:
    def __init__(self, connection):
        self.metadata = MetaData()
        self.metadata.reflect(bind=connection, only=TABLE_NAMES)

    def table(self, name):
        if name not in TABLE_NAMES:
            raise StorageValidationError("지원하지 않는 관리 테이블입니다.")
        return self.metadata.tables[name]

    def key_filter(self, table, key):
        """기본키 전체를 사용해 조회·변경 대상 행을 지정한다."""
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
        """실제 컬럼 정의에 맞춰 필수값, 자료형과 길이를 확인한다."""
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
        table = self.table(name)
        query = select(table).where(self.key_filter(table, key))
        if for_update:
            query = query.with_for_update()
        row = session.execute(query).mappings().one_or_none()
        if row is None:
            raise LookupError(f"{name}: 해당 기록이 없습니다.")
        return dict(row)

    def insert(self, session, name, values):
        """입력값을 검사해 저장하고 생성된 행의 기본키를 반환한다."""
        table = self.table(name)
        self.validate_columns(table, values, inserting=True)
        result = session.execute(table.insert().values(**values))
        return dict(zip(table.primary_key.columns.keys(), result.inserted_primary_key))

    def update(self, session, name, key, values):
        table = self.table(name)
        self.validate_columns(table, values)
        changes = {field: value for field, value in values.items() if field not in table.primary_key.columns}
        if changes:
            session.execute(table.update().where(self.key_filter(table, key)).values(**changes))

    def delete(self, session, name, key):
        table = self.table(name)
        session.execute(table.delete().where(self.key_filter(table, key)))
