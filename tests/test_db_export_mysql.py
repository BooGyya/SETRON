"""DB 전체 SQL 내보내기를 실제 MySQL 5.6.19에서 내보내고 빈 DB에 복원해 검증한다 (Jira S15P31B106-76)."""

import os
from datetime import datetime

import pymysql
import pytest
from pymysql.constants import CLIENT
from sqlalchemy import MetaData, inspect

from setron.features.ai.schemas import ExtractKind
from setron.features.db_export.service import export_database
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import Database, DatabaseConfig
from test_import_save_mysql import EQUIPMENT, request, row, storage  # noqa: F401  시드 적용 fixture 재사용
from test_schema_mysql import schema_database  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")

TRICKY = "작은따옴표' 큰따옴표\" 역슬래시\\ 줄바꿈\n이모지😀 세미콜론; -- 주석처럼"


def connect():
    config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
    return pymysql.connect(host=config.host, port=config.port, user=config.username, password=config.password,
                           database=config.database, charset="utf8mb4", client_flag=CLIENT.MULTI_STATEMENTS)


def run_sql(text):
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(text)
            while cursor.nextset():
                pass
        connection.commit()
    finally:
        connection.close()


def contents():
    """모든 관리 테이블의 행과 AUTO_INCREMENT 값."""
    connection = connect()
    try:
        with connection.cursor() as cursor:
            result = {}
            for table in TABLE_NAMES:
                cursor.execute(f"SELECT * FROM `{table}` ORDER BY 1")
                result[table] = cursor.fetchall()
                cursor.execute("SELECT AUTO_INCREMENT FROM information_schema.TABLES "
                               "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
                result[table, "auto_increment"] = cursor.fetchone()[0]
            return result
    finally:
        connection.close()


@pytest.fixture(scope="module")
def filled(storage, schema_database):  # noqa: F811
    saver = DatabaseImportSaver(storage)
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [
        row(1, {"equipment_code": "C1", "version_raw": "F1"}, **{**EQUIPMENT, "line": TRICKY},
            equipment_code="EXP-1", model_name="M-O2", family_name="O2", version_raw="v1")], "exp-1",
        cells={"C1": "EXP-1", "F1": TRICKY}))
    saver.save(request(ExtractKind.RELEASE_NOTE, [
        row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v1", content=TRICKY,
            model_name="M-O2", family_name="O2", release_date="2026-09-01")], "exp-2"))
    return schema_database


def test_export_restores_identical_data_into_empty_db(filled, tmp_path):
    before = contents()
    path = tmp_path / "백업.sql"

    result = export_database(filled, path, now=lambda: datetime(2026, 11, 3, 10, 0))

    assert result.saved and result.message.startswith("테이블 13개")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("-- SETRON DB 내보내기\n-- 생성 2026-11-03 10:00:00")
    assert "DROP TABLE `" not in text
    assert not list(tmp_path.glob("*.part"))

    _drop_all(filled)
    run_sql(text)

    assert contents() == before
    assert any(TRICKY in str(value) for value in before["item"][0])


def test_running_dump_on_existing_tables_changes_nothing(filled, tmp_path):
    path = tmp_path / "again.sql"
    assert export_database(filled, path).saved
    before = contents()

    with pytest.raises(pymysql.err.OperationalError, match="already exists"):
        run_sql(path.read_text(encoding="utf-8"))

    assert contents() == before


def test_wrong_name_and_unwritable_path_are_reported(filled, tmp_path):
    assert export_database(filled, tmp_path / "백업.txt").message == "파일 이름은 .sql로 끝나야 합니다."

    folder = tmp_path / "폴더.sql"
    folder.mkdir()
    result = export_database(filled, folder)
    assert not result.saved and result.message
    assert not list(tmp_path.glob("*.part"))


def test_db_failure_is_reported():
    database = Database.from_env({"SETRON_DB_HOST": "127.0.0.1", "SETRON_DB_PORT": "1", "SETRON_DB_NAME": "x",
                                  "SETRON_DB_USER": "u", "SETRON_DB_PASSWORD": "p", "SETRON_DB_CONNECT_TIMEOUT": "1"})
    result = export_database(database, "백업.sql")

    assert not result.saved and result.message.startswith("DB 자료를 읽지 못했습니다")


def _drop_all(database):
    metadata = MetaData()
    metadata.reflect(bind=database.engine, only=set(inspect(database.engine).get_table_names()) & set(TABLE_NAMES))
    metadata.drop_all(database.engine)
