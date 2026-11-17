"""SQL 내보내기의 파일 보존과 오류 안내를 외부 DB 없이 검증한다."""

import errno
import tempfile
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from setron.features.db_export.service import export_database
from setron.features.export.schemas import ExportStatus
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseError


@pytest.fixture
def export_source():
    """SQLAlchemy 연결 대신 관리 테이블의 조회 결과를 전달한다."""
    database = MagicMock()
    connection = MagicMock()
    database.engine.connect.return_value = connection
    connection.execution_options.return_value = connection
    connection.__enter__.return_value = connection
    connection.invalidated = False
    cursors = {}
    rows = {
        "equipment": [(1, "장비 원문")],
        "app_setting": [(1, '{"warning_years": 1, "danger_years": 2}')],
        "risk_rule": [(1, "환경 안전 위험 사유")],
    }

    def execute(statement, *args, **kwargs):
        result = MagicMock()
        if statement == "SELECT VERSION()":
            result.scalar.return_value = "5.6.19"
        elif statement == "SELECT DATABASE()":
            result.scalar.return_value = "setron_test"
        elif statement == "SELECT @@SESSION.sql_mode":
            result.scalar.return_value = "STRICT_TRANS_TABLES,NO_BACKSLASH_ESCAPES"
        elif statement.startswith("SELECT COUNT(*) FROM"):
            table = statement.split("`")[1]
            result.scalar.return_value = len(rows.get(table, []))
        elif statement.startswith("SHOW CREATE TABLE"):
            table = statement.split("`")[1]
            result.one.return_value = (
                table, f"CREATE TABLE `{table}` (`id` int, `value` text) ENGINE=InnoDB",
            )
        elif statement.startswith("SELECT * FROM"):
            table = statement.split("`")[1]
            result.keys.return_value = ("id", "value")
            result.fetchmany.side_effect = [rows.get(table, []), []]
            result.__enter__.return_value = result
            cursors[table] = result
        return result

    connection.exec_driver_sql.side_effect = execute
    return database, connection, cursors


def protected_files(tmp_path):
    """기존 SQL과 이전 임시파일 규칙에 해당하는 별개 파일을 준비한다."""
    path = tmp_path / "백업.sql"
    path.write_bytes(b"previous complete backup")
    unrelated = tmp_path / "백업.sql.part"
    unrelated.write_bytes(b"unrelated user file")
    return path, unrelated


def assert_protected(path, unrelated):
    assert path.read_bytes() == b"previous complete backup"
    assert unrelated.read_bytes() == b"unrelated user file"
    assert set(path.parent.iterdir()) == {path, unrelated}


def test_success_exports_all_tables_and_updates_only_the_destination(export_source, tmp_path):
    database, _, _ = export_source
    path, unrelated = protected_files(tmp_path)

    result = export_database(database, path, now=lambda: datetime(2026, 11, 3, 10, 20, 30))

    assert result.status is ExportStatus.SAVED and result.saved
    assert result.path == path
    sql = path.read_text(encoding="utf-8")
    assert "2026-11-03 10:20:30" in sql
    assert "장비 원문" in sql
    assert "환경 안전 위험 사유" in sql
    assert "warning_years" in sql and "danger_years" in sql
    for table in TABLE_NAMES:
        assert f"CREATE TABLE `{table}`" in sql
    assert not any(line.startswith("DROP TABLE") for line in sql.splitlines())
    assert unrelated.read_bytes() == b"unrelated user file"
    assert set(tmp_path.iterdir()) == {path, unrelated}


def test_wrong_extension_fails_before_connecting(export_source, tmp_path):
    database, _, _ = export_source
    path = tmp_path / "백업.txt"

    result = export_database(database, path)

    assert not result.saved and ".sql" in result.message
    database.engine.connect.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("error", [
    OperationalError("SELECT VERSION()", {}, RuntimeError("connection lost")),
    DatabaseError("DB 연결을 확인해 주세요."),
])
def test_query_failure_reports_reason_and_keeps_existing_files(export_source, tmp_path, error):
    database, connection, _ = export_source
    path, unrelated = protected_files(tmp_path)
    connection.exec_driver_sql.side_effect = error

    result = export_database(database, path)

    assert not result.saved and "DB" in result.message
    assert_protected(path, unrelated)


@pytest.mark.parametrize("error", [
    OperationalError("connect", {}, RuntimeError("connection refused")),
    RuntimeError("authentication library unavailable"),
])
def test_connection_failure_is_reported_without_touching_existing_files(export_source, tmp_path, error):
    database, _, _ = export_source
    path, unrelated = protected_files(tmp_path)
    database.engine.connect.side_effect = error

    result = export_database(database, path)

    assert not result.saved and "DB" in result.message
    assert_protected(path, unrelated)


def test_reading_later_chunk_fails_without_publishing_partial_dump(export_source, tmp_path):
    database, connection, _ = export_source
    path, unrelated = protected_files(tmp_path)
    execute = connection.exec_driver_sql.side_effect

    def fail_after_rows(statement, *args, **kwargs):
        result = execute(statement, *args, **kwargs)
        if statement.startswith("SELECT * FROM `equipment`"):
            result.fetchmany.side_effect = [
                [(1, "읽은 자료")],
                OperationalError(statement, {}, RuntimeError("connection lost during read")),
            ]
        return result

    connection.exec_driver_sql.side_effect = fail_after_rows

    result = export_database(database, path)

    assert not result.saved and "DB" in result.message
    assert_protected(path, unrelated)


def test_permission_failure_is_reported_and_does_not_remove_other_files(export_source, tmp_path, monkeypatch):
    database, _, _ = export_source
    path, unrelated = protected_files(tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr("setron.features.db_export.service.NamedTemporaryFile", denied)

    result = export_database(database, path)

    assert not result.saved and result.message
    assert_protected(path, unrelated)


def test_write_failure_does_not_publish_an_incomplete_dump(export_source, tmp_path, monkeypatch):
    database, _, _ = export_source
    path, unrelated = protected_files(tmp_path)
    create_temp = tempfile.NamedTemporaryFile

    def disk_full(*args, **kwargs):
        stream = create_temp(*args, **kwargs)
        write = stream.write
        writes = 0

        def write_then_fail(text):
            nonlocal writes
            writes += 1
            if writes > 1:
                raise OSError(errno.ENOSPC, "No space left on device")
            return write(text)

        stream.write = write_then_fail
        return stream

    monkeypatch.setattr("setron.features.db_export.service.NamedTemporaryFile", disk_full)

    result = export_database(database, path)

    assert not result.saved and result.message
    assert_protected(path, unrelated)


@pytest.mark.parametrize("error", [
    PermissionError(errno.EACCES, "Permission denied"),
    OSError(errno.ENOSPC, "No space left on device"),
])
def test_replace_failure_preserves_previous_backup_and_cleans_own_temp_file(
    export_source, tmp_path, monkeypatch, error,
):
    database, _, _ = export_source
    path, unrelated = protected_files(tmp_path)

    def failed_replace(*args, **kwargs):
        raise error

    monkeypatch.setattr("setron.features.db_export.service.os.replace", failed_replace)

    result = export_database(database, path)

    assert not result.saved and result.message
    assert_protected(path, unrelated)


def test_missing_destination_folder_returns_a_failure(export_source, tmp_path):
    database, _, _ = export_source
    path = tmp_path / "없는 폴더" / "백업.sql"

    result = export_database(database, path)

    assert not result.saved and result.message
    assert not path.parent.exists()
