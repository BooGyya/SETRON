"""DB 전체 SQL 내보내기를 실제 MySQL 5.6.19에서 내보내고 빈 DB에 복원해 검증한다 (Jira S15P31B106-76)."""

import json
import os
import re
from threading import Event, Thread
from datetime import date, datetime

import pymysql
import pytest
from pymysql.constants import CLIENT
from sqlalchemy import event

from setron.features.ai.schemas import ExtractKind
from setron.features.db_export.service import export_database
from setron.features.db_import.dump import read_dump
from setron.features.peer_sync import is_sync_key, read_state
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


def run_sql(text, *, sql_mode=None):
    connection = connect()
    try:
        with connection.cursor() as cursor:
            if sql_mode is not None:
                cursor.execute("SET SESSION sql_mode = %s", (sql_mode,))
            # 파일 전체를 한 패킷으로 보내지 않고 내보내기가 만든 명령 단위로 실행한다.
            # 데이터의 줄바꿈은 SQL 리터럴 안에서 이스케이프되어 명령 시작 행과 구분된다.
            for statement in re.split(r"(?m)(?=^(?:SET |CREATE TABLE |INSERT INTO ))", text):
                if not statement.startswith(("SET ", "CREATE TABLE ", "INSERT INTO ")):
                    continue
                cursor.execute(statement)
                while cursor.nextset():
                    pass
            if sql_mode is not None:
                cursor.execute("SELECT @@SESSION.sql_mode")
                assert set(cursor.fetchone()[0].split(",")) == set(sql_mode.split(","))
        connection.commit()
    finally:
        connection.close()


def contents():
    """업무 행과 AUTO_INCREMENT. 교환 경계에서 추가되는 예약 이력은 별도로 검사한다."""
    connection = connect()
    try:
        with connection.cursor() as cursor:
            result = {}
            for table in TABLE_NAMES:
                cursor.execute(f"SELECT * FROM `{table}` ORDER BY 1")
                records = cursor.fetchall()
                result[table] = tuple(row for row in records if table != "app_setting" or not is_sync_key(row[0]))
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
        row(1, {"equipment_code": "C1", "version_raw": "F1"}, edited=("version_raw",), **{**EQUIPMENT, "line": TRICKY},
            equipment_code="EXP-1", model_name="M-O2", family_name="O2", version_raw="v1",
            pm_name="PM1")], "exp-1",
        # 특수문자 원문은 대조하지 않는 셀(Z9)에 둔다. 저장 값과 연결된 셀은 원본과 같아야 한다.
        cells={"C1": "EXP-1", "F1": "v1", "Z9": TRICKY}))
    saver.save(request(ExtractKind.EQUIPMENT_STATUS, [
        row(2, {"equipment_code": "C2", "version_raw": "F2"},
            **{**EQUIPMENT, "base_date": "2026-10-22", "production_area": None},
            equipment_code="EXP-1", model_name="M-O2", family_name="O2", version_raw="v2",
            pm_name="PM1")], "exp-history", cells={"C2": "EXP-1", "F2": "v2"}))
    saver.save(request(ExtractKind.RELEASE_NOTE, [
        row(1, {"version_raw": "A2", "content": "D2"}, version_raw="v1", content=TRICKY,
            model_name="M-O2", family_name="O2", release_date="2026-09-01")], "exp-2",
        cells={"A2": "v1", "D2": TRICKY}))
    with storage.transaction() as tx:
        release = tx.session.connection().exec_driver_sql(
            "SELECT r.release_version_id, r.family_id, r.batch_id, f.group_id "
            "FROM release_version r JOIN version_family f ON f.family_id = r.family_id "
            "ORDER BY r.release_version_id LIMIT 1"
        ).one()
        item_id = tx.session.connection().exec_driver_sql("SELECT item_id FROM item ORDER BY item_id LIMIT 1").scalar()
        # 원본과 구분한 현재 수정값, 바이너리 원문·위험 근거·비기본 설정도 모두 보존해야 한다.
        note = tx.insert("release_note", {"group_id": release.group_id, "batch_id": release.batch_id,
                                          "release_date": date(2026, 9, 1)})
        tx.update("release_version", {"release_version_id": release.release_version_id},
                  {"release_note_id": note["release_note_id"]})
        tx.update("item", {"item_id": item_id}, {"content": "수정한 내용 " + TRICKY,
                  "edited_fields": ["content"], "release_note_id": note["release_note_id"]})
        tx.insert("equipment", {"equipment_code": "설비😀'\\끝 "})
        tx.insert("risk_rule", {
            "family_id": release.family_id, "rule_type": "DEFECT_ITEM", "item_id": item_id,
            "start_release_version_id": release.release_version_id, "risk_reason": TRICKY,
            "target_reference": {"원본 근거": TRICKY}, "mapping_status": "VERIFIED",
        })
        tx.update("app_setting", {"setting_key": "version_age_thresholds"},
                  {"setting_value": {"warning": 2, "danger": 4, "unit": "YEAR"}})
        tx.insert("app_setting", {"setting_key": "한글 설정😀", "setting_value": {"원문": TRICKY}})
    return schema_database


def test_export_restores_identical_data_into_empty_db(filled, tmp_path):
    before = contents()
    assert all(before[table] for table in TABLE_NAMES), {table: len(before[table]) for table in TABLE_NAMES}
    assert len(before["equipment_snapshot"]) == 2
    assert len(before["equipment_pm"]) == 2
    path = tmp_path / "백업.sql"

    result = export_database(filled, path, now=lambda: datetime(2026, 11, 3, 10, 0))

    assert result.saved and result.message.startswith("테이블 13개")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("-- SETRON DB 내보내기\n-- 생성 2026-11-03 10:00:00")
    assert "DROP TABLE `" not in text
    state = read_state(read_dump(path))
    assert state.entities and state.events
    assert not list(tmp_path.glob("*.part"))

    _drop_all(filled)
    run_sql(text, sql_mode="NO_BACKSLASH_ESCAPES,ANSI_QUOTES")

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


def test_export_ignores_source_sql_mode_and_restores_it(filled, tmp_path):
    """원본과 복원 연결의 따옴표 해석이 달라도 데이터와 연결 설정이 보존된다."""
    with filled.engine.connect() as connection:
        original_mode = connection.exec_driver_sql("SELECT @@SESSION.sql_mode").scalar()
        connection.exec_driver_sql("SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES,ANSI_QUOTES'")
        connection.commit()
    try:
        before = contents()
        path = tmp_path / "sql_mode.sql"
        assert export_database(filled, path).saved
        with filled.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT @@SESSION.sql_mode").scalar() == "ANSI_QUOTES,NO_BACKSLASH_ESCAPES"
        _drop_all(filled)
        run_sql(path.read_text(encoding="utf-8"))
        assert contents() == before
    finally:
        with filled.engine.connect() as connection:
            connection.exec_driver_sql("SET SESSION sql_mode = %s", (original_mode,))


def test_concurrent_save_does_not_mix_export_snapshots(filled, tmp_path):
    """교환 이력과 업무 자료를 잠근 동안 다른 연결은 기다리고 출력은 기존값을 유지한다."""
    before = contents()
    changed = False
    started, finished, committed = Event(), Event(), Event()
    errors, writers = [], []

    def save():
        writer = None
        try:
            writer = connect()
            with writer.cursor() as control:
                started.set()
                control.execute("UPDATE app_setting SET setting_value = %s WHERE setting_key = %s",
                                ('{"warning":3,"danger":5,"unit":"YEAR"}', b"version_age_thresholds"))
            writer.commit()
            committed.set()
        except Exception as error:
            errors.append(error)
        finally:
            if writer is not None:
                writer.close()
            finished.set()

    def save_during_export(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if statement.startswith("SELECT * FROM `import_batch` ORDER BY 1") and not changed:
            changed = True
            writer = Thread(target=save, daemon=True)
            writers.append(writer)
            writer.start()
            assert started.wait(3)
            assert not finished.wait(.2), "다른 연결이 내보내기 잠금을 기다려야 합니다."

    # 연결 기본값이 READ COMMITTED인 경우에도 하나의 스냅샷을 사용한다.
    database = Database(filled.engine.execution_options(isolation_level="READ COMMITTED"))
    event.listen(filled.engine, "before_cursor_execute", save_during_export)
    try:
        path = tmp_path / "snapshot.sql"
        result = export_database(database, path)
    finally:
        event.remove(filled.engine, "before_cursor_execute", save_during_export)
        for writer in writers:
            writer.join(10)
    assert result.saved and changed
    assert finished.is_set() and committed.is_set() and not errors
    assert contents() != before
    _drop_all(filled)
    run_sql(path.read_text(encoding="utf-8"))
    assert contents() == before


def test_large_multichunk_dump_restores_all_rows(filled, tmp_path):
    """긴 원문이 있는 수백 행도 메모리·INSERT 묶음을 나누며 값 손실 없이 내보낸다."""
    value = json.dumps({"원문": TRICKY * 150}, ensure_ascii=False)
    with filled.engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO app_setting (setting_key, setting_value) VALUES (%s, %s)",
                                   [(f"chunk_{number}".encode(), value) for number in range(405)])
    before = contents()
    path = tmp_path / "large.sql"
    assert export_database(filled, path).saved
    sql = path.read_text(encoding="utf-8")
    assert sql.count("INSERT INTO `app_setting`") > 2
    _drop_all(filled)
    run_sql(sql)
    assert contents() == before


def _drop_all(database):
    # 테스트가 만든 관리 테이블을 외래키의 역순으로 정리한다. 반사 조회는 ANSI_QUOTES에 의존한다.
    with database.engine.begin() as connection:
        for table in reversed(TABLE_NAMES):
            connection.exec_driver_sql(f"DROP TABLE `{table}`")
