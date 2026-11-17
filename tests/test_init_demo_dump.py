"""Windows 초기화의 확인·백업·대상 격리를 DB 접속 없이 검증한다."""

from copy import deepcopy
from datetime import datetime
import importlib
from pathlib import Path
import re
import sys

import pymysql
import pytest

from setron.features.storage.validation import TABLE_NAMES
from setron.features.db_import.dump import DUMP_COLUMNS, DumpFormatError
from setron.infrastructure.database import DatabaseConfig
from test_sql_import_dump import _export

DATABASE = Path(__file__).resolve().parents[1] / "database"


@pytest.fixture(scope="module")
def initializer():
    sys.path.insert(0, str(DATABASE))
    try:
        return importlib.import_module("init_demo_dump")
    finally:
        sys.path.remove(str(DATABASE))


def dataset(path, *, code=b"NEW"):
    path.write_text(_export({"equipment": [(1, code)]}), encoding="utf-8")
    return path


class FakeConnection:
    def __init__(self, rows=None, *, objects=None, platform="Win64", missing=False, lock=1):
        self.rows = deepcopy(rows or {table: () for table in TABLE_NAMES})
        self.objects = dict(objects if objects is not None else ((table, "BASE TABLE") for table in TABLE_NAMES))
        self.platform, self.missing, self.lock = platform, missing, lock
        self.log, self.selected = [], []
        self.unsupported = set()
        self.fail_create = 0
        self.bad_count = False
        self.reset_bad_count = False
        self.deleted_any = False
        self.fail_delete = None
        self.foreign_key_checks = 1
        self.engines = dict.fromkeys(TABLE_NAMES, "InnoDB")
        self.columns = dict(DUMP_COLUMNS)
        self.grants = ("GRANT ALL PRIVILEGES ON *.* TO 'demo'@'localhost'",)
        self.transaction_rows = None
        self.backup_guard = None
        self._cursor = FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self._cursor

    def select_db(self, name):
        self.log.append(("SELECT_DB", name))
        if self.missing:
            self.missing = False
            raise pymysql.err.OperationalError(1049, "Unknown database")
        self.selected.append(name)

    def commit(self):
        self.log.append(("COMMIT", None))
        self.transaction_rows = None

    def begin(self):
        self.log.append(("BEGIN", None))
        assert self.transaction_rows is None
        self.transaction_rows = deepcopy(self.rows)

    def rollback(self):
        self.log.append(("ROLLBACK", None))
        if self.transaction_rows is not None:
            self.rows = self.transaction_rows
            self.transaction_rows = None


class FakeCursor:
    def __init__(self, connection):
        self.connection, self.result = connection, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        db = self.connection
        db.log.append((sql, params))
        self.result = []
        if sql == "SELECT @@version_compile_os":
            self.result = [(db.platform,)]
        elif sql == "SHOW GRANTS FOR CURRENT_USER":
            self.result = [(grant,) for grant in db.grants]
        elif sql.startswith("SELECT GET_LOCK"):
            self.result = [(db.lock,)]
        elif sql == "SHOW FULL TABLES":
            self.result = list(db.objects.items())
        elif sql.startswith("SELECT COUNT(*) FROM information_schema."):
            catalog = sql.split("information_schema.", 1)[1].split()[0]
            self.result = [(int(catalog in db.unsupported),)]
        elif sql == "SELECT VERSION()":
            self.result = [("5.6.19",)]
        elif sql == "SELECT @@SESSION.FOREIGN_KEY_CHECKS":
            self.result = [(db.foreign_key_checks,)]
        elif sql.startswith("SET FOREIGN_KEY_CHECKS ="):
            db.foreign_key_checks = params[0] if params else int(sql.rsplit(" ", 1)[1])
        elif sql.startswith("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES"):
            self.result = list(db.engines.items())
        elif sql.startswith("SHOW COLUMNS FROM"):
            table = re.search(r"`([a-z_]+)`", sql)[1]
            self.result = [(column, "text") for column in db.columns[table]]
        elif sql.startswith("SELECT COUNT(*) FROM `"):
            table = re.search(r"FROM `([a-z_]+)`", sql)[1]
            count = len(db.rows[table])
            wrong = (db.bad_count or db.reset_bad_count and db.deleted_any) and table == "equipment"
            self.result = [(count + int(wrong),)]
        elif sql.startswith("SHOW CREATE TABLE"):
            table = re.search(r"`([a-z_]+)`", sql)[1]
            columns = ",\n".join(f"  `{column}` text" for column in db.columns[table])
            self.result = [(table, f"CREATE TABLE `{table}` (\n{columns}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")]
        elif sql.startswith("SELECT `"):
            table = re.search(r"FROM `([a-z_]+)`", sql)[1]
            columns = re.findall(r"`([a-z_]+)`", sql.split(" FROM ", 1)[0])
            self.result = [tuple(row[column] for column in columns) for row in db.rows[table]]
        elif sql.startswith("DROP TABLE IF EXISTS"):
            if db.backup_guard:
                db.backup_guard()
            table = re.search(r"`([a-z_]+)`", sql)[1]
            db.objects.pop(table, None)
            db.rows[table] = ()
        elif sql.startswith("DELETE FROM"):
            if db.backup_guard:
                db.backup_guard()
            table = re.search(r"`([a-z_]+)`", sql)[1]
            if db.fail_delete == table:
                raise pymysql.err.OperationalError(1213, "fake delete failure")
            db.rows[table] = ()
            db.deleted_any = True
        elif sql.startswith("CREATE TABLE"):
            if db.fail_create:
                db.fail_create -= 1
                raise pymysql.err.OperationalError(1005, "fake creation failure")
            table = re.match(r"CREATE TABLE `?([a-z_]+)`?", sql)[1]
            db.objects[table] = "BASE TABLE"
            db.rows[table] = ()
            columns = re.findall(r"^  `([a-z_]+)` ", sql, re.M) or re.findall(r"^    ([a-z_]+) [A-Z]+", sql, re.M)
            db.columns[table] = tuple(columns)

    def executemany(self, sql, values):
        db = self.connection
        db.log.append((sql, tuple(values)))
        table = re.search(r"INSERT INTO `([a-z_]+)`", sql)[1]
        columns = re.findall(r"`([a-z_]+)`", sql.split(" VALUES ", 1)[0])[1:]
        db.rows[table] = (*db.rows[table], *(dict(zip(columns, row, strict=True)) for row in values))

    def fetchone(self):
        return self.result.pop(0) if self.result else None

    def fetchall(self):
        rows, self.result = self.result, []
        return rows


@pytest.fixture
def setup(initializer, tmp_path, monkeypatch):
    source = dataset(tmp_path / "SETRON_DB.sql")
    old = initializer.read_dataset(dataset(tmp_path / "old.sql", code="OLD😀'\\\n".encode()))
    db = FakeConnection(old)
    connections = []
    def connect(**kwargs):
        connections.append(kwargs)
        return db
    monkeypatch.setattr(initializer.pymysql, "connect", connect)
    config = DatabaseConfig("127.0.0.1", 3306, "chosen_db", "demo", "secret")
    return source, old, db, config, connections


def destructive(db):
    return [sql for sql, _ in db.log if sql.startswith(("DROP ", "CREATE TABLE", "INSERT INTO", "DELETE FROM", "TRUNCATE ", "ALTER "))]


def test_external_dump_wins_and_invalid_external_never_falls_back(initializer, setup, tmp_path):
    source, _, db, config, connections = setup
    assert initializer.dump_path(tmp_path) == source
    source.write_text("DROP DATABASE other;", encoding="utf-8")
    with pytest.raises(DumpFormatError):
        initializer.prepare(config, folder=tmp_path, confirm=lambda _: True)
    assert not connections and not db.log


def test_trusted_schema_keeps_complete_table_comment_after_internal_semicolon(initializer):
    statements = initializer.schema_statements()
    source_lines = initializer.SCHEMA.read_text(encoding="utf-8").splitlines()
    endings = [line for line in source_lines if line.startswith(") ENGINE=")]
    assert len(endings) == len(statements) == len(TABLE_NAMES)
    assert [statement.splitlines()[-1] for statement in statements] == endings
    equipment = statements[TABLE_NAMES.index("equipment")]
    assert "COMMENT='실제 설비;" in equipment
    assert equipment.endswith("';")  # COMMENT 안의 ; 뒤까지 보존하여 MySQL이 완전한 DDL을 받는다.


@pytest.mark.parametrize("confirm", [None, lambda _: False])
def test_existing_data_without_approval_never_creates_backup_or_deletes(initializer, setup, tmp_path, confirm):
    _, old, db, config, _ = setup
    result = initializer.prepare(config, folder=tmp_path, confirm=confirm)
    assert not result.applied and db.rows == old
    assert not destructive(db) and not (tmp_path / "backups").exists()
    assert any(sql.startswith("SELECT RELEASE_LOCK") for sql, _ in db.log)


def test_quiet_mode_never_approves_existing_data(initializer, setup, tmp_path, monkeypatch):
    _, old, db, config, _ = setup
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer, "load_config", lambda: config)
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", "--quiet"])
    assert initializer.main() == 3
    assert db.rows == old and not destructive(db)


@pytest.mark.parametrize("objects", [
    {"unrelated": "BASE TABLE"},
    {**dict.fromkeys(TABLE_NAMES, "BASE TABLE"), "unrelated": "BASE TABLE"},
    {**dict.fromkeys(TABLE_NAMES, "BASE TABLE"), "equipment": "VIEW"},
    dict.fromkeys(TABLE_NAMES[:-1], "BASE TABLE"),
])
def test_unrelated_partial_or_view_objects_abort_before_confirmation(initializer, setup, tmp_path, objects):
    _, _, db, config, _ = setup
    db.objects = objects
    with pytest.raises(initializer.SetupError, match="테이블·뷰"):
        initializer.prepare(config, folder=tmp_path, confirm=lambda _: pytest.fail("확인창 전에 중단해야 함"))
    assert not destructive(db) and not (tmp_path / "backups").exists()


@pytest.mark.parametrize("unsupported", ["TRIGGERS", "ROUTINES", "EVENTS", "KEY_COLUMN_USAGE"])
def test_unsupported_objects_and_other_database_references_abort(initializer, setup, tmp_path, unsupported):
    _, _, db, config, _ = setup
    db.unsupported.add(unsupported)
    with pytest.raises(initializer.SetupError):
        initializer.prepare(config, folder=tmp_path, confirm=lambda _: True)
    assert not destructive(db)


def test_non_windows_server_and_failed_lock_never_change_database(initializer, setup, tmp_path):
    _, _, db, config, _ = setup
    db.platform = "Linux"
    with pytest.raises(initializer.SetupError, match="Windows"):
        initializer.prepare(config, folder=tmp_path)
    assert not db.selected and not destructive(db)
    db.platform, db.lock = "Win64", 0
    with pytest.raises(initializer.SetupError, match="이미 실행"):
        initializer.prepare(config, folder=tmp_path)
    assert not db.selected and not destructive(db)


def test_backup_failure_prevents_all_deletion(initializer, setup, tmp_path, monkeypatch):
    _, old, db, config, _ = setup
    def fail(*args):
        raise OSError("fake disk failure")
    monkeypatch.setattr(initializer, "_backup", fail)
    with pytest.raises(initializer.SetupError, match="백업·검증에 실패"):
        initializer.prepare(config, folder=tmp_path, confirm=lambda _: True)
    assert db.rows == old and not destructive(db)


def test_approved_replacement_has_verified_backup_before_first_drop(initializer, setup, tmp_path):
    source, old, db, config, connections = setup
    confirmations = []
    def guard():
        files = list((tmp_path / "backups").glob("*.sql"))
        assert len(files) == 1 and initializer.read_dataset(files[0]) == old
    db.backup_guard = guard
    result = initializer.prepare(config, folder=tmp_path, confirm=lambda message: confirmations.append(message) or True)
    assert result.applied and result.backup.is_file()
    assert initializer.read_dataset(result.backup) == old
    assert db.rows == initializer.read_dataset(source)
    assert "chosen_db" in confirmations[0] and "3306" in confirmations[0] and "덮어쓰시겠습니까?" in confirmations[0]
    assert "백업 위치:" in confirmations[0] and str(tmp_path / "backups") in confirmations[0]
    drops = [sql for sql, _ in db.log if sql.startswith("DROP TABLE")]
    assert drops == [f"DROP TABLE IF EXISTS `{table}`" for table in reversed(TABLE_NAMES)]
    assert db.selected == [config.database] and connections[0].get("client_flag", 0) == 0
    assert all(not sql.startswith("USE ") for sql, _ in db.log)


def test_apply_failure_restores_original_values_and_keeps_backup(initializer, setup, tmp_path):
    _, old, db, config, _ = setup
    db.fail_create = 1
    with pytest.raises(initializer.SetupError, match="자동 복원했습니다") as error:
        initializer.prepare(config, folder=tmp_path, confirm=lambda _: True)
    assert db.rows == old and set(db.objects) == set(TABLE_NAMES)
    files = list((tmp_path / "backups").glob("*.sql"))
    assert len(files) == 1 and str(files[0]) in str(error.value)
    assert initializer.read_dataset(files[0]) == old


def test_failed_automatic_restore_reports_existing_backup(initializer, setup, tmp_path):
    _, old, db, config, _ = setup
    db.fail_create = 2
    with pytest.raises(initializer.SetupError, match="자동 복원을 완료하지 못했습니다") as error:
        initializer.prepare(config, folder=tmp_path, confirm=lambda _: True)
    files = list((tmp_path / "backups").glob("*.sql"))
    assert len(files) == 1 and str(files[0]) in str(error.value)
    assert initializer.read_dataset(files[0]) == old


def test_empty_database_uses_only_configured_name_and_trusted_schema(initializer, setup, tmp_path):
    source, _, db, config, _ = setup
    body = source.read_text(encoding="utf-8")
    source.write_text("CREATE DATABASE IF NOT EXISTS setron\n DEFAULT CHARACTER SET utf8mb4\n COLLATE utf8mb4_unicode_ci;\nUSE setron;\n\n" + body, encoding="utf-8")
    db.objects, db.missing = {}, True
    db.rows = dict.fromkeys(TABLE_NAMES, ())
    result = initializer.prepare(config, folder=tmp_path)
    assert result.applied and result.backup is None
    creates = [sql for sql, _ in db.log if sql.startswith("CREATE DATABASE")]
    assert len(creates) == 1 and "`chosen_db`" in creates[0] and " setron" not in creates[0]
    assert not any(sql.startswith("USE ") for sql, _ in db.log)
    assert not any(sql.startswith("DROP ") for sql, _ in db.log)
    assert all(sql.startswith("CREATE TABLE ") and not sql.startswith("CREATE TABLE `")
               for sql, _ in db.log if sql.startswith("CREATE TABLE"))


def test_backup_names_are_unique_and_round_trip_binary_text_and_dates(initializer, setup, tmp_path):
    _, old, db, config, _ = setup
    db.rows["import_batch"] = ({"batch_id": 1, "source_type": "MANUAL", "imported_at": datetime(2026, 11, 12, 9, 4, 3),
                               "raw_data": '{"원본":{"A1":null,"B1":false,"C1":0}}'},)
    first, rows, _ = initializer._backup(db, db.cursor(), tmp_path, config)
    second, again, _ = initializer._backup(db, db.cursor(), tmp_path, config)
    assert first != second and first.is_file() and second.is_file()
    assert rows == again and rows["equipment"] == old["equipment"]
    assert rows["import_batch"][0]["imported_at"] == "2026-11-12 09:04:03"


def test_missing_foreign_key_is_rejected_before_connect(initializer, setup, tmp_path):
    source, _, db, config, connections = setup
    source.write_text(_export({"version_family": [(1, 999, "missing model", None)]}), encoding="utf-8")
    with pytest.raises(initializer.SetupError, match="없는 자료"):
        initializer.prepare(config, folder=tmp_path, confirm=lambda _: True)
    assert not connections and not db.log


def test_validate_dump_uses_external_and_bundled_data_without_reading_config_or_connecting(initializer, setup, tmp_path, monkeypatch, capsys):
    source, _, db, _, connections = setup
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer, "load_config", lambda: pytest.fail("검증은 설정을 읽으면 안 됨"))
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", "--validate-dump"])
    assert initializer.main() == 0
    assert "설비 1대" in capsys.readouterr().out
    bundled = tmp_path / "dataset.dump"
    source.rename(bundled)
    monkeypatch.setattr(initializer, "DUMP", bundled)
    assert initializer.main() == 0
    assert not connections and not db.log


def test_row_count_mismatch_cannot_report_success(initializer, setup, tmp_path, monkeypatch):
    _, _, db, config, _ = setup
    db.objects = {}
    db.bad_count = True
    with pytest.raises(initializer.SetupError, match="적용에 실패"):
        initializer.prepare(config, folder=tmp_path)
    assert not db.objects


def test_packager_takes_explicit_dump_and_copies_only_deployment_files(initializer, setup, tmp_path, monkeypatch):
    source, _, _, _, connections = setup
    spec = importlib.util.spec_from_file_location("build_demo_db_for_test", DATABASE.parent / "packaging" / "build_demo_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "deployment"
    monkeypatch.setattr(sys, "argv", ["build_demo_db.py", "--dump", str(source), "--output-dir", str(output)])
    commands = []
    build_root = tmp_path / "build_source"
    build_root.mkdir()
    (build_root / "prepare_demo_db.bat").write_bytes(b"@echo off\nsetlocal DisableDelayedExpansion\n")
    monkeypatch.setattr(module, "ROOT", build_root)
    def build(command, **kwargs):
        commands.append(command)
        location = Path(command[command.index("--distpath") + 1])
        location.mkdir()
        (location / "SETRON_DB_INIT.exe").write_bytes(b"test-executable")
    monkeypatch.setattr(module.subprocess, "run", build)
    # Windows 가상화 임시 경로의 ACL 대신 테스트별 OS 임시 폴더를 쓴다.
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    module.main()
    assert {file.name for file in output.iterdir()} == {"SETRON_DB_INIT.exe", "SETRON_DB.sql", "prepare_demo_db.bat"}
    assert (output / "SETRON_DB.sql").read_bytes() == source.read_bytes()
    assert (output / "prepare_demo_db.bat").read_bytes() == b"@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
    assert "--console" in commands[0]
    assert sum("dataset.dump" in part for part in commands[0]) == 1
    assert sum("schema_mysql5619.sql" in part for part in commands[0]) == 1
    assert not connections


def test_reset_does_not_read_demo_sql_and_preserves_all_table_objects(initializer, setup, tmp_path, monkeypatch):
    source, old, db, config, _ = setup
    source.unlink()
    monkeypatch.setattr(initializer, "dump_path", lambda *_: pytest.fail("초기화는 시연 SQL에 의존하면 안 됨"))
    objects = deepcopy(db.objects)
    confirmations = []
    def guard():
        backups = list((tmp_path / "backups").glob("*.sql"))
        assert len(backups) == 1 and initializer.read_dataset(backups[0]) == old
    db.backup_guard = guard
    result = initializer.reset_data(config, folder=tmp_path, confirm=lambda text: confirmations.append(text) or True)
    assert result.applied and initializer.read_dataset(result.backup) == old
    assert db.objects == objects and all(not rows for rows in db.rows.values())
    mutations = destructive(db)
    assert mutations == [f"DELETE FROM `{table}`" for table in reversed(TABLE_NAMES)]
    assert "테이블 구조를 유지하고 모든 데이터를 삭제하시겠습니까?" in confirmations[0]
    assert "chosen_db" in confirmations[0] and "3306" in confirmations[0]
    assert "모든 데이터" in confirmations[0] and str(tmp_path / "backups") in confirmations[0]
    assert [event for event, _ in db.log if event in {"BEGIN", "COMMIT"}] == ["COMMIT", "BEGIN", "COMMIT"]
    assert db.foreign_key_checks == 1


@pytest.mark.parametrize("confirm", [None, lambda _: False])
def test_reset_without_confirmation_never_backs_up_or_deletes(initializer, setup, tmp_path, confirm):
    source, old, db, config, _ = setup
    source.unlink()
    result = initializer.reset_data(config, folder=tmp_path, confirm=confirm)
    assert not result.applied and db.rows == old and not destructive(db)
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("options", [["--reset-data", "--quiet"], ["--quiet", "--reset-data"], ["--import-demo", "--quiet"]])
def test_action_flags_with_quiet_never_delete_existing_data(initializer, setup, tmp_path, monkeypatch, options):
    _, old, db, config, _ = setup
    monkeypatch.setattr(initializer, "program_folder", lambda: tmp_path)
    monkeypatch.setattr(initializer, "load_config", lambda: config)
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", *options])
    assert initializer.main() == 3
    assert db.rows == old and not destructive(db)


@pytest.mark.parametrize("options", [
    ["--reset-data", "--import-demo"], ["--reset-data", "--validate-dump"],
    ["--reset-data", "--reset-data"], ["--reset-data", "--yes"], ["--unknown"],
])
def test_invalid_or_conflicting_flags_abort_before_config_and_database(initializer, setup, monkeypatch, options):
    _, _, db, _, connections = setup
    monkeypatch.setattr(initializer, "load_config", lambda: pytest.fail("옵션 오류 전에 설정을 읽으면 안 됨"))
    monkeypatch.setattr(sys, "argv", ["SETRON_DB_INIT.exe", *options])
    assert initializer.main() == 2
    assert not connections and not db.log


def test_reset_backup_failure_prevents_row_deletion(initializer, setup, tmp_path, monkeypatch):
    _, old, db, config, _ = setup
    def fail(*args):
        raise OSError("fake disk failure")
    monkeypatch.setattr(initializer, "_backup", fail)
    with pytest.raises(initializer.SetupError, match="백업·검증에 실패"):
        initializer.reset_data(config, folder=tmp_path, confirm=lambda _: True)
    assert db.rows == old and not destructive(db)
    assert not any(event == "BEGIN" for event, _ in db.log)


@pytest.mark.parametrize("foreign_key_checks", [0, 1])
def test_reset_partial_delete_failure_rolls_back_all_rows_and_restores_fk_setting(initializer, setup, tmp_path, foreign_key_checks):
    _, old, db, config, _ = setup
    db.foreign_key_checks = foreign_key_checks
    db.fail_delete = "equipment_group"  # equipment의 기존 행이 삭제된 뒤 실패하도록 한다.
    objects = deepcopy(db.objects)
    with pytest.raises(initializer.SetupError, match="트랜잭션의 취소") as error:
        initializer.reset_data(config, folder=tmp_path, confirm=lambda _: True)
    assert db.rows == old and db.objects == objects
    assert "DELETE FROM `equipment`" in destructive(db)
    assert db.foreign_key_checks == foreign_key_checks
    events = [event for event, _ in db.log if event in {"BEGIN", "COMMIT", "ROLLBACK"}]
    assert events == ["COMMIT", "BEGIN", "ROLLBACK"]
    backups = list((tmp_path / "backups").glob("*.sql"))
    assert len(backups) == 1 and str(backups[0]) in str(error.value)
    assert initializer.read_dataset(backups[0]) == old


def test_reset_nonzero_verification_rolls_back_without_structure_changes(initializer, setup, tmp_path):
    _, old, db, config, _ = setup
    db.reset_bad_count = True
    with pytest.raises(initializer.SetupError, match="초기화에 실패"):
        initializer.reset_data(config, folder=tmp_path, confirm=lambda _: True)
    assert db.rows == old and set(db.objects) == set(TABLE_NAMES)
    assert all(sql.startswith("DELETE FROM") for sql in destructive(db))
    assert db.foreign_key_checks == 1


@pytest.mark.parametrize("problem", ["empty", "partial", "extra", "view", "myisam", "columns", "cross-db", "trigger", "missing-db", "linux"])
def test_reset_rejects_unsafe_target_before_confirmation(initializer, setup, tmp_path, problem):
    _, old, db, config, _ = setup
    if problem == "empty":
        db.objects = {}
    elif problem == "partial":
        db.objects.pop("app_setting")
    elif problem == "extra":
        db.objects["unrelated"] = "BASE TABLE"
    elif problem == "view":
        db.objects["equipment"] = "VIEW"
    elif problem == "myisam":
        db.engines["equipment"] = "MyISAM"
    elif problem == "columns":
        db.columns["equipment"] = ("equipment_id", "unexpected")
    elif problem == "cross-db":
        db.unsupported.add("KEY_COLUMN_USAGE")
    elif problem == "trigger":
        db.unsupported.add("TRIGGERS")
    elif problem == "missing-db":
        db.missing = True
    elif problem == "linux":
        db.platform = "Linux"
    with pytest.raises(initializer.SetupError):
        initializer.reset_data(config, folder=tmp_path, confirm=lambda _: pytest.fail("위험한 대상은 확인창 전 중단해야 함"))
    assert db.rows == old and not destructive(db)
    assert not any(sql.startswith("CREATE DATABASE") for sql, _ in db.log)
    assert not (tmp_path / "backups").exists()


def test_legacy_item_backup_and_failed_new_load_restore_exact_old_columns_and_values(initializer, setup, tmp_path):
    from setron.features.db_import.dump import LEGACY_ITEM_COLUMNS
    _, _, db, config, _ = setup
    old_item = {"item_id": 7, "release_note_id": None, "item_no": 3, "content": "원본 본문😀", "batch_id": 1,
                "source_reference": None, "edited_fields": "[]"}
    db.rows["item"] = (old_item,)
    db.columns["item"] = LEGACY_ITEM_COLUMNS
    old_rows = deepcopy(db.rows)
    db.fail_create = 1
    with pytest.raises(initializer.SetupError, match="자동 복원했습니다"):
        initializer.prepare(config, folder=tmp_path, confirm=lambda _: True)
    assert db.rows == old_rows and db.columns["item"] == LEGACY_ITEM_COLUMNS
    backup, = (tmp_path / "backups").glob("*.sql")
    assert "`jira_key`" not in backup.read_text(encoding="utf-8")
    restored = initializer.read_dataset(backup)
    assert restored["item"] == (old_item,)


def test_legacy_backup_can_be_imported_into_new_schema_with_null_jira(initializer, setup, tmp_path):
    from setron.features.db_import.dump import LEGACY_ITEM_COLUMNS
    _, _, db, config, _ = setup
    db.rows["item"] = ({"item_id": 1, "release_note_id": None, "item_no": 1, "content": "원본", "batch_id": 1,
                        "source_reference": None, "edited_fields": "[]"},)
    db.columns["item"] = LEGACY_ITEM_COLUMNS
    _, rows, _ = initializer._backup(db, db.cursor(), tmp_path, config)
    initializer._drop(db.cursor())
    initializer._apply(db, db.cursor(), rows, initializer.schema_statements())
    assert db.columns["item"] == DUMP_COLUMNS["item"] and db.rows["item"][0]["jira_code"] is None


def test_nonnull_jira_cannot_be_discarded_by_restoring_old_item_structure(initializer, setup, tmp_path):
    from setron.features.db_import.dump import LEGACY_ITEM_COLUMNS
    _, _, db, config, _ = setup
    db.columns["item"] = LEGACY_ITEM_COLUMNS
    _, rows, definitions = initializer._backup(db, db.cursor(), tmp_path, config)
    rows["item"] = ({"item_id": 1, "release_note_id": None, "item_no": 1, "jira_key": "ETOJ-3257", "content": "원본",
                     "batch_id": 1, "source_reference": None, "edited_fields": "[]"},)
    db.log.clear()
    with pytest.raises(DumpFormatError, match="손실 없이"):
        initializer._apply(db, db.cursor(), rows, definitions)
    assert not db.log


@pytest.mark.parametrize("layout", ["old", "key", "code", "renamed", "dual"])
def test_backup_and_restore_keep_each_historical_jira_layout_exact(initializer, setup, tmp_path, layout):
    from setron.features.db_import.dump import (
        DUAL_ITEM_COLUMNS, LEGACY_ITEM_COLUMNS, LEGACY_KEY_ITEM_COLUMNS, LEGACY_RENAMED_CODE_ITEM_COLUMNS,
    )
    _, _, db, config, _ = setup
    columns = {"old": LEGACY_ITEM_COLUMNS, "key": LEGACY_KEY_ITEM_COLUMNS,
               "code": DUMP_COLUMNS["item"], "renamed": LEGACY_RENAMED_CODE_ITEM_COLUMNS,
               "dual": DUAL_ITEM_COLUMNS}[layout]
    item = {"item_id": 7, "release_note_id": None, "item_no": 3, "content": "원본 본문😀", "batch_id": 1,
            "source_reference": None, "edited_fields": "[]"}
    for field in ("jira_key", "jira_code"):
        if field in columns:
            item[field] = "ETOJ-1, ETOJ-2"
    db.rows["item"], db.columns["item"] = (item,), columns
    before = deepcopy(db.rows)
    _, backed_up, definitions = initializer._backup(db, db.cursor(), tmp_path, config)
    assert backed_up == before
    initializer._drop(db.cursor())
    initializer._apply(db, db.cursor(), backed_up, definitions)
    assert db.rows == before and db.columns["item"] == columns
    initializer._drop(db.cursor())
    initializer._apply(db, db.cursor(), backed_up, initializer.schema_statements())
    assert db.rows["item"][0]["jira_code"] == (None if layout == "old" else "ETOJ-1, ETOJ-2")
    assert backed_up == before


def test_apply_rejects_long_jira_before_ddl_for_a_legacy_fifty_character_column(initializer, setup, tmp_path):
    from setron.features.db_import.dump import LEGACY_KEY_ITEM_COLUMNS
    _, _, db, config, _ = setup
    db.columns["item"] = LEGACY_KEY_ITEM_COLUMNS
    _, rows, definitions = initializer._backup(db, db.cursor(), tmp_path, config)
    definitions = tuple(definition.replace("`jira_key` text", "`jira_key` varchar(50)") for definition in definitions)
    rows["item"] = ({"item_id": 1, "release_note_id": None, "item_no": 1, "content": "원문", "batch_id": 1,
                     "source_reference": None, "edited_fields": "[]", "jira_code": "TEAM-" + "1" * 60},)
    db.log.clear()
    with pytest.raises(initializer.SetupError, match="손실 없이"):
        initializer._apply(db, db.cursor(), rows, definitions)
    assert db.log == []


def test_old_columnless_dump_derives_original_jira_only_when_restoring_to_new_schema(initializer, setup, tmp_path):
    import json
    _, _, db, _, _ = setup
    raw = {"노트": {"A1": "No", "B1": "Update내용", "C1": "JIRA",
                    "A2": 1, "B2": "원본 내용", "C2": "ETOJ-987"}}
    references = {"content": {"sheet": "노트", "cells": ["B2"]}}
    path = tmp_path / "old-with-original.sql"
    path.write_text(_export({
        "import_batch": [(1, "EXCEL", datetime(2026, 11, 12), json.dumps(raw, ensure_ascii=False))],
        "item": [(1, None, 1, "원본 내용", 1, json.dumps(references, ensure_ascii=False), "[]")],
    }, legacy_item=True), encoding="utf-8")
    rows = initializer.read_dataset(path)
    before = deepcopy(rows)
    assert "jira_code" not in rows["item"][0] and "jira_key" not in rows["item"][0]
    initializer._drop(db.cursor())
    initializer._apply(db, db.cursor(), rows, initializer.schema_statements())
    assert db.rows["item"][0]["jira_code"] == "ETOJ-987"
    assert rows == before


def test_oversized_derived_jira_is_rejected_before_connect_or_drop(initializer, setup, tmp_path, monkeypatch):
    import json
    _, _, db, config, _ = setup
    raw = {"노트": {"A1": "Update내용", "B1": "JIRA", "A2": "원문", "B2": "TEAM-" + "1" * 201}}
    source = tmp_path / "long-original.sql"
    source.write_text(_export({
        "import_batch": [(1, "EXCEL", datetime(2026, 11, 12), json.dumps(raw))],
        "item": [(1, None, 1, "원문", 1, json.dumps({"content": {"sheet": "노트", "cells": ["A2"]}}), "[]")],
    }, legacy_item=True), encoding="utf-8")
    monkeypatch.setattr(initializer.pymysql, "connect", lambda **_: pytest.fail("Jira 사전 검증 전 DB 접속 금지"))
    before = deepcopy(db.rows)
    with pytest.raises(DumpFormatError, match="자르지 않고"):
        initializer.prepare(config, folder=tmp_path, source=source, confirm=lambda _: True)
    assert db.rows == before and not db.log and not (tmp_path / "backups").exists()
