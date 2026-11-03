"""DB 전체를 SQL 파일로 내보낸다 (Jira S15P31B106-76, REQ-054·REQ-041, 멘토링 2차).

받은 사람이 빈 DB에서 파일을 실행하면 모든 관리 테이블과 자료가 그대로 들어간다.

정한 규칙
- 관리 테이블(:data:`TABLE_NAMES`) 전체의 구조(`SHOW CREATE TABLE`)와 모든 행을 한 트랜잭션에서 읽는다.
- 파일은 **빈 DB용**이다. `DROP TABLE`을 넣지 않으므로, 같은 테이블이 이미 있는 DB에서 실행하면 첫 `CREATE TABLE`에서
  멈추고 기존 자료를 바꾸지 않는다. 다른 PC 자료의 가져오기·병합은 보류(REQ-055)라 이번 범위가 아니다.
- 외래키 순서와 무관하게 넣을 수 있도록 실행 중에만 외래키 검사를 끈다. AUTO_INCREMENT 값은 그대로 둔다.
- 임시 파일에 다 쓴 뒤 이름을 바꾸므로, 실패해도 반쯤 쓴 파일이 남지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseError

ROWS_PER_INSERT = 200


def export_database(database, path: str | Path, *, now: Callable[[], datetime] = datetime.now) -> ExportResult:
    """``database``(:class:`Database`)의 관리 테이블 전체를 ``path``(.sql)에 저장한다.

    실패해도 예외 대신 원인을 담은 결과를 돌려준다.
    """
    path = Path(path)
    if path.suffix.lower() != ".sql":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .sql로 끝나야 합니다.")
    temp = path.with_name(path.name + ".part")
    try:
        with database.transaction() as session, temp.open("w", encoding="utf-8", newline="\n") as file:
            connection = session.connection()
            raw = connection.connection.dbapi_connection
            version = connection.exec_driver_sql("SELECT VERSION()").scalar()
            name = connection.exec_driver_sql("SELECT DATABASE()").scalar()
            tables = [(table, *_table(connection, raw, table)) for table in TABLE_NAMES]
            total = sum(len(rows) for _, _, _, rows in tables)
            file.write(_header(now(), version, name, len(tables), total))
            for table, create, columns, rows in tables:
                file.write(f"\n-- 테이블 {table} · {len(rows)}행\n{create};\n")
                for start in range(0, len(rows), ROWS_PER_INSERT):
                    values = ",\n".join("(" + ", ".join(raw.escape(v) for v in row) + ")"
                                        for row in rows[start:start + ROWS_PER_INSERT])
                    file.write(f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in columns)}) VALUES\n{values};\n")
            file.write("\nSET FOREIGN_KEY_CHECKS = @OLD_FOREIGN_KEY_CHECKS;\n")
        os.replace(temp, path)
    except PermissionError:
        _remove(temp)
        return ExportResult(ExportStatus.FAILED, path,
                            "파일을 저장할 수 없습니다. 같은 이름의 파일이 열려 있는지, 폴더 쓰기 권한이 있는지 확인해 주세요.")
    except DatabaseError as error:
        _remove(temp)
        return ExportResult(ExportStatus.FAILED, path, f"DB 자료를 읽지 못했습니다: {error}")
    except OSError as error:
        _remove(temp)
        return ExportResult(ExportStatus.FAILED, path, f"파일을 저장하지 못했습니다: {error.strerror or error}")
    return ExportResult(ExportStatus.SAVED, path, f"테이블 {len(tables)}개 · {total}행")


def _table(connection, raw, table: str):
    create = connection.exec_driver_sql(f"SHOW CREATE TABLE `{table}`").one()[1]
    result = raw.cursor()
    try:
        result.execute(f"SELECT * FROM `{table}` ORDER BY 1")
        columns = [d[0] for d in result.description]
        rows = list(result.fetchall())
    finally:
        result.close()
    return create, columns, rows


def _header(created: datetime, version: str, name: str, tables: int, rows: int) -> str:
    return (
        "-- SETRON DB 내보내기\n"
        f"-- 생성 {created:%Y-%m-%d %H:%M:%S} · 원본 DB {name} · MySQL {version}\n"
        f"-- 테이블 {tables}개 · {rows}행\n"
        "--\n"
        "-- 빈 DB에서 실행하세요. DROP TABLE이 없으므로 같은 테이블이 있는 DB에서는 첫 CREATE TABLE에서 멈추고\n"
        "-- 기존 자료를 바꾸지 않습니다. 실행 중에만 외래키 검사를 끕니다.\n\n"
        "SET NAMES utf8mb4;\n"
        "SET @OLD_FOREIGN_KEY_CHECKS = @@FOREIGN_KEY_CHECKS;\n"
        "SET FOREIGN_KEY_CHECKS = 0;\n"
    )


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
