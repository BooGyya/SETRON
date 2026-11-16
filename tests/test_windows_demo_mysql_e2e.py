"""Opt-in Windows 5.6.19 E2E; an existing MySQL instance is never used.

Set SETRON_WINDOWS_MYSQL_HOME and SETRON_WINDOWS_DEMO_DUMP explicitly.
SETRON_WINDOWS_PACKAGE_DIR additionally enables the frozen EXE/BAT round trip.
Every server file and credential belongs to a TemporaryDirectory, removed after
the owned process stops. There is no connection to port 3306 or 13306.
"""

from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from hashlib import sha1, sha256
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pymysql
import pytest

from setron.features.db_import.dump import DUMP_COLUMNS
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseConfig
from test_init_demo_dump import initializer  # noqa: F401 - shared module fixture


pytestmark = pytest.mark.skipif(
    sys.platform != "win32"
    or not os.environ.get("SETRON_WINDOWS_MYSQL_HOME")
    or not os.environ.get("SETRON_WINDOWS_DEMO_DUMP"),
    reason="Explicit Windows MySQL home and 300-equipment dump opt-in required.",
)

_DATABASES = {"qa_source", "qa_binary", "qa_fresh", "qa_pending"}
_PRIVILEGES = "SELECT, CREATE, DROP, INSERT, REFERENCES, DELETE, TRIGGER"
_LINES = {"P1F", "P1D", "P4H", "P4D"}


def _child_env(folder):
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("SETRON_DB_", "SETRON_TEST_DB_", "SETRON_READ_DB_", "SETRON_AI_", "MYSQL_"))
    }
    env.update(TEMP=str(folder), TMP=str(folder), PYTHONIOENCODING="utf-8")
    return env


def _normalized(rows):
    """Compare all values without depending on export INSERT ordering."""
    result = {}
    for table, records in rows.items():
        values = [
            {key: value.isoformat(sep=" ") if isinstance(value, datetime)
             else value.isoformat() if isinstance(value, date) else value
             for key, value in row.items()}
            for row in records
        ]
        if table == "equipment_snapshot":
            # The approved demo predates this nullable column. Fresh schema
            # projection adds NULL; retain every explicit (including non-NULL)
            # value and keep the original parsed dump dictionaries unchanged.
            for row in values:
                row.setdefault("process_name", None)
        keys = ("release_version_id", "item_id") if table == "release_version_item" else (DUMP_COLUMNS[table][0],)
        result[table] = tuple(sorted(values, key=lambda row: tuple(row[key] for key in keys)))
    return result


def _assert_rows_equal(actual, expected):
    """Report the first different cell in every differing row, without JSON dumps."""
    differences = []
    for table in sorted(set(actual) | set(expected)):
        left, right = actual.get(table, ()), expected.get(table, ())
        if len(left) != len(right):
            differences.append(f"{table}: actual rows={len(left)}, expected rows={len(right)}")
        for index, (got, want) in enumerate(zip(left, right), 1):
            for column in dict.fromkeys((*want, *got)):
                if column not in got or column not in want or got[column] != want[column]:
                    identity = got.get(DUMP_COLUMNS[table][0], want.get(DUMP_COLUMNS[table][0]))
                    def preview(row):
                        if column not in row:
                            return "<missing column>"
                        value = row[column]
                        if column in {"raw_data", "source_reference", "setting_value", "target_reference", "content"}:
                            return f"<{type(value).__name__}, value omitted>"
                        return f"{type(value).__name__}:{repr(value)[:180]}"
                    differences.append(
                        f"{table}[row={index}, id={identity}].{column}: "
                        f"actual {preview(got)}; expected {preview(want)}"
                    )
                    break
    assert not differences, "Applied values differ from source SQL:\n" + "\n".join(differences)


@dataclass(repr=False)
class _WindowsServer:
    root: Path
    home: Path
    dump: Path
    expected: dict
    password: str = field(repr=False)
    app_password: str = field(repr=False)
    username: str
    port: int = 0
    process: object = None
    console: object = None
    sentinel: str = ""

    @property
    def data(self):
        return self.root / "data"

    def own_path(self, path):
        path = Path(path).resolve()
        assert path != self.root and path.is_relative_to(self.root), "QA path escaped its temporary directory"
        return path

    def connect(self):
        assert self.port not in (0, 3306, 13306)
        return pymysql.connect(
            host="127.0.0.1", port=self.port, user="root", password=self.password,
            charset="utf8mb4", autocommit=True, connect_timeout=1,
            read_timeout=15, write_timeout=15,
        )

    def guard_connection(self, connection):
        assert self.process is not None and self.process.poll() is None, "Owned QA server is not running"
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@version, @@version_compile_os, @@port, @@datadir, CURRENT_USER()")
            version, platform, port, datadir, account = cursor.fetchone()
        assert version == "5.6.19" and platform.lower().startswith("win"), "Unexpected QA server version/platform"
        assert port == self.port and port not in (3306, 13306), "Unexpected QA server port"
        assert Path(datadir).resolve() == self.data.resolve(), "Refusing a server outside the owned QA datadir"
        assert account == "root@127.0.0.1", "Unexpected QA administrator identity"

    def guard(self):
        with closing(self.connect()) as connection:
            self.guard_connection(connection)

    def admin(self, sql, params=None):
        with closing(self.connect()) as connection:
            self.guard_connection(connection)  # no SQL mutation before identity/path proof
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()

    def config(self, database):
        assert database in _DATABASES
        self.guard()
        return DatabaseConfig("127.0.0.1", self.port, database, self.username, self.app_password)

    def snapshot(self, database):
        assert database in _DATABASES
        definitions, rows = {}, {}
        with closing(self.connect()) as connection:
            self.guard_connection(connection)
            with connection.cursor() as cursor:
                cursor.execute(f"SHOW FULL TABLES FROM `{database}`")
                assert dict(cursor.fetchall()) == dict.fromkeys(TABLE_NAMES, "BASE TABLE")
                for table in TABLE_NAMES:
                    cursor.execute(f"SHOW CREATE TABLE `{database}`.`{table}`")
                    definitions[table] = cursor.fetchone()[1]
                    cursor.execute(f"SELECT * FROM `{database}`.`{table}`")
                    columns = tuple(column[0] for column in cursor.description)
                    rows[table] = tuple(dict(zip(columns, row, strict=True)) for row in cursor.fetchall())
        self.assert_sentinel()
        return definitions, _normalized(rows)

    def assert_sentinel(self):
        values = self.admin("SELECT token FROM `unrelated_db`.`sentinel`")
        assert values == ((self.sentinel,),), "An unrelated QA database changed"
        tables = self.admin("SHOW FULL TABLES FROM `unrelated_db`")
        assert tables == (("sentinel", "BASE TABLE"),)

    def assert_seed(self, database):
        definitions, rows = self.snapshot(database)
        _assert_rows_equal(rows, _normalized(self.expected))
        assert {table: len(values) for table, values in rows.items()} == {
            table: len(values) for table, values in self.expected.items()
        }
        with closing(self.connect()) as connection:
            self.guard_connection(connection)
            with connection.cursor() as cursor:
                cursor.execute(f"SHOW COLUMNS FROM `{database}`.`item` LIKE 'jira_code'")
                jira = cursor.fetchone()
                assert jira and jira[1].lower() == "varchar(200)" and jira[2] == "YES"
                cursor.execute(f"SELECT COUNT(*) FROM `{database}`.`item` WHERE jira_code IS NOT NULL AND jira_code <> ''")
                assert cursor.fetchone()[0] > 0
                cursor.execute(
                    f"SELECT s.line, COUNT(DISTINCT s.equipment_id) FROM `{database}`.`equipment_snapshot` s "
                    f"JOIN (SELECT equipment_id, MAX(base_date) base_date FROM `{database}`.`equipment_snapshot` "
                    "GROUP BY equipment_id) latest ON latest.equipment_id = s.equipment_id AND latest.base_date = s.base_date "
                    "WHERE s.site = %s AND s.line IN (%s,%s,%s,%s) GROUP BY s.line",
                    ("평택", *sorted(_LINES)),
                )
                lines = dict(cursor.fetchall())
                assert set(lines) == _LINES and all(count > 0 for count in lines.values())
        return definitions, rows

    def write_env(self, folder, database, *, password=None):
        folder = self.own_path(folder)
        assert database in _DATABASES
        (folder / "setron.env").write_text(
            f"SETRON_DB_HOST=127.0.0.1\nSETRON_DB_PORT={self.port}\n"
            f"SETRON_DB_NAME={database}\nSETRON_DB_USER={self.username}\n"
            f"SETRON_DB_PASSWORD={self.app_password if password is None else password}\n",
            encoding="utf-8",
        )

    def invoke(self, folder, answers, *, batch=False, extra_secrets=()):
        folder = self.own_path(folder)
        self.guard()
        executable = folder / "SETRON_DB_INIT.exe"
        if batch:
            cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
            # cwd already identifies the owned Korean/space-containing folder.
            # list2cmdline escapes embedded quotes for the CRT, whereas cmd.exe
            # treats those backslashes literally. An ASCII relative BAT name
            # avoids that incompatible extra quoting layer entirely.
            args = [str(cmd), "/d", "/c", r".\prepare_demo_db.bat"]
        else:
            args = [str(executable)]
        result = subprocess.run(
            args, input=answers.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=folder, env=_child_env(self.root / "tmp"), timeout=150,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        decoded, non_utf8 = [], []
        for name, value in (("stdout", result.stdout), ("stderr", result.stderr)):
            try:
                decoded.append(value.decode("utf-8"))
            except UnicodeDecodeError:
                # cmd can fail before BAT's chcp 65001. Keep this a test failure,
                # but expose its actual Windows error instead of a decode stack.
                decoded.append(value.decode("mbcs", errors="backslashreplace"))
                non_utf8.append(name)
        output, errors = decoded
        if any(secret in output or secret in errors for secret in (self.password, self.app_password, *extra_secrets)):
            pytest.fail("A credential appeared in program output; all output is withheld")
        assert not non_utf8, (
            f"Process output is not UTF-8 ({', '.join(non_utf8)}, exit={result.returncode}): "
            f"{errors or output}"
        )
        assert not errors, f"Frozen helper unexpectedly wrote to stderr (exit={result.returncode}): {errors}"
        self.assert_sentinel()
        return result.returncode, output

    def stop(self):
        if self.process is None:
            if self.console is not None:
                self.console.close()
            return
        if self.process.poll() is None:
            try:
                self.guard()  # never send shutdown to a replaced/unrelated endpoint
            except Exception:
                pass
            else:
                config = self.own_path(self.root / "admin.cnf")
                config.write_text(
                    f"[client]\nprotocol=tcp\nhost=127.0.0.1\nport={self.port}\nuser=root\npassword={self.password}\n",
                    encoding="ascii",
                )
                try:
                    subprocess.run(
                        [str(self.home / "bin/mysqladmin.exe"), f"--defaults-file={config}",
                         "--protocol=tcp", "--host=127.0.0.1", f"--port={self.port}", "--user=root", "shutdown"],
                        cwd=self.root, env=_child_env(self.root / "tmp"), timeout=15,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()  # only this fixture's Popen process handle
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
        if self.console is not None:
            self.console.close()


@pytest.fixture(scope="module")
def windows_mysql(initializer):
    if sys.platform != "win32" or not all(os.environ.get(name) for name in (
        "SETRON_WINDOWS_MYSQL_HOME", "SETRON_WINDOWS_DEMO_DUMP",
    )):
        pytest.skip("Isolated Windows MySQL E2E opt-in is not configured")
    home = Path(os.environ["SETRON_WINDOWS_MYSQL_HOME"]).resolve()
    source = Path(os.environ["SETRON_WINDOWS_DEMO_DUMP"]).resolve()
    for relative in ("bin/mysqld.exe", "bin/mysqladmin.exe", "share/mysql_system_tables.sql", "share/mysql_system_tables_data.sql"):
        assert (home / relative).is_file(), f"Missing MySQL distribution component: {relative}"
    assert source.is_file(), "Explicit demo SQL does not exist"
    expected = initializer.read_dataset(source)
    assert set(expected) == set(TABLE_NAMES) and len(expected["equipment"]) == 300
    assert len(expected["equipment_snapshot"]) == 1800
    assert sum(map(len, expected.values())) == 9869, "This E2E requires the approved 300-equipment demo dataset"
    initializer._check_references(expected, initializer.schema_statements())
    base = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="setron-win56-", dir=base) as temporary:
        root = Path(temporary).resolve()
        assert root != base and root.is_relative_to(base)
        assert not root.is_relative_to(home) and not home.is_relative_to(root)
        server = _WindowsServer(root, home, source, expected, secrets.token_hex(32), secrets.token_hex(24), "qa" + secrets.token_hex(5))
        try:
            (server.data / "mysql").mkdir(parents=True)
            (root / "tmp").mkdir()
            common = [
                str(home / "bin/mysqld.exe"), "--no-defaults", f"--basedir={home}", f"--datadir={server.data}",
                f"--tmpdir={root / 'tmp'}", f"--innodb-data-home-dir={server.data}",
                f"--innodb-log-group-home-dir={server.data}", "--innodb-flush-method=normal",
                "--innodb-buffer-pool-size=32M", "--innodb-log-file-size=8M", "--performance-schema=OFF", "--skip-log-bin",
            ]
            native_hash = "*" + sha1(sha1(server.password.encode("ascii")).digest()).hexdigest().upper()
            sql = "USE mysql;\n" + (home / "share/mysql_system_tables.sql").read_text(encoding="utf-8")
            sql += "\nUSE mysql;\n" + (home / "share/mysql_system_tables_data.sql").read_text(encoding="utf-8")
            sql += (
                "\nUSE mysql;\nDELETE FROM mysql.user WHERE NOT (User='root' AND Host='127.0.0.1');\n"
                f"UPDATE mysql.user SET Password='{native_hash}', plugin='mysql_native_password', "
                "authentication_string='', password_expired='N' WHERE User='root' AND Host='127.0.0.1';\n"
                "DELETE FROM mysql.db;\nDELETE FROM mysql.proxies_priv;\n"
            )
            bootstrap = subprocess.run(
                [*common, "--bootstrap", "--skip-networking", "--default-storage-engine=MyISAM",
                 "--max-allowed-packet=8M", "--net-buffer-length=16K", f"--log-error={root / 'bootstrap.err'}"],
                input=sql.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=root, env=_child_env(root / "tmp"), timeout=90, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert bootstrap.returncode == 0, "Fresh MySQL bootstrap failed; credential-bearing diagnostics are withheld"
            del sql, native_hash, bootstrap
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                server.port = listener.getsockname()[1]
            assert server.port not in (3306, 13306)
            server.console = (root / "server-console.log").open("wb")
            server.process = subprocess.Popen(
                [*common, "--bind-address=127.0.0.1", f"--port={server.port}", "--skip-name-resolve",
                 f"--pid-file={root / 'server.pid'}", f"--log-error={root / 'server.err'}", "--console"],
                cwd=root, env=_child_env(root / "tmp"), stdout=server.console, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            deadline = time.monotonic() + 45
            while True:
                assert server.process.poll() is None, "Owned MySQL process exited before readiness"
                try:
                    server.guard()
                    break
                except pymysql.MySQLError:
                    if time.monotonic() >= deadline:
                        pytest.fail("Owned MySQL did not become ready; credentials and server diagnostics are withheld")
                    time.sleep(0.1)
            accounts = server.admin("SELECT Host, User, plugin, password_expired, Password <> '' FROM mysql.user")
            assert accounts == (("127.0.0.1", "root", "mysql_native_password", "N", 1),)
            server.admin("CREATE USER %s@'127.0.0.1' IDENTIFIED BY %s", (server.username, server.app_password))
            for database in sorted(_DATABASES):
                assert not server.admin("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", (database,))
                server.admin(f"GRANT {_PRIVILEGES} ON `{database}`.* TO %s@'127.0.0.1'", (server.username,))
            server.admin("CREATE DATABASE `unrelated_db`")
            server.admin("CREATE TABLE `unrelated_db`.`sentinel` (token VARCHAR(80) NOT NULL) ENGINE=InnoDB")
            server.sentinel = secrets.token_hex(24)
            server.admin("INSERT INTO `unrelated_db`.`sentinel` VALUES (%s)", (server.sentinel,))
            yield server
            server.assert_sentinel()
        finally:
            server.stop()


def _backups(folder):
    return set((folder / "backups").glob("*.sql"))


def _assert_backup(initializer, folder, path, expected):
    assert path and path.is_file() and path.parent == folder / "backups"
    assert _normalized(initializer.read_dataset(path)) == expected


def test_windows_mysql_source_prepare_reset_round_trip(windows_mysql, initializer, monkeypatch):
    server = windows_mysql
    folder = server.own_path(server.root / "source-program")
    folder.mkdir()
    shutil.copyfile(server.dump, folder / "SETRON_DB.sql")
    config = server.config("qa_source")
    first = initializer.prepare(config, folder=folder)
    assert first.applied and first.backup is None
    original = server.assert_seed("qa_source")
    backups = _backups(folder)
    assert not initializer.prepare(config, folder=folder, confirm=lambda _: False).applied
    assert _backups(folder) == backups and server.snapshot("qa_source") == original
    overwritten = initializer.prepare(config, folder=folder, confirm=lambda _: True)
    assert overwritten.applied
    _assert_backup(initializer, folder, overwritten.backup, original[1])
    assert server.assert_seed("qa_source") == original
    backups = _backups(folder)
    assert not initializer.reset_data(config, folder=folder, confirm=lambda _: False).applied
    assert _backups(folder) == backups and server.snapshot("qa_source") == original
    reset = initializer.reset_data(config, folder=folder, confirm=lambda _: True)
    assert reset.applied
    _assert_backup(initializer, folder, reset.backup, original[1])
    definitions, empty = server.snapshot("qa_source")
    assert definitions == original[0] and all(not values for values in empty.values())
    assert initializer.prepare(config, folder=folder, confirm=lambda _: True).applied
    assert server.assert_seed("qa_source") == original
    wrong = secrets.token_hex(24)
    with monkeypatch.context() as isolated:
        isolated.setattr(initializer, "load_config", lambda: replace(config, password=wrong))
        isolated.setattr(initializer, "program_folder", lambda: folder)
        code, message = initializer.run(confirm=lambda _: True)
    assert code == 1 and "1045" in message and "[계정·권한 확인]" in message
    assert all(secret not in message for secret in (wrong, server.password, server.app_password))
    assert server.snapshot("qa_source") == original
    print(f"Isolated Windows source API: 13 tables / {sum(map(len, original[1].values()))} rows; 300 equipment / 1800 snapshots; import/cancel/backup/reset/1045 verified")


@pytest.mark.skipif(not os.environ.get("SETRON_WINDOWS_PACKAGE_DIR"), reason="Frozen Windows helper directory opt-in required")
def test_windows_mysql_frozen_exe_and_batch_round_trip(windows_mysql, initializer):
    server = windows_mysql
    package = Path(os.environ["SETRON_WINDOWS_PACKAGE_DIR"]).resolve()
    folder = server.own_path(server.root / "한글 경로" / "program")
    folder.mkdir(parents=True)
    for name in ("SETRON_DB_INIT.exe", "prepare_demo_db.bat", "SETRON_DB.sql"):
        assert (package / name).is_file(), f"Missing packaged helper component: {name}"
        shutil.copyfile(package / name, folder / name)
    assert (folder / "SETRON_DB.sql").read_bytes() == server.dump.read_bytes()
    server.write_env(folder, "qa_binary")
    code, message = server.invoke(folder, "1\n\n")
    assert code == 0 and "시연 데이터 삽입 완료" in message
    original = server.assert_seed("qa_binary")
    backups = _backups(folder)
    code, message = server.invoke(folder, "1\nN\n\n", batch=True)
    assert code == 3 and "취소" in message
    assert _backups(folder) == backups and server.snapshot("qa_binary") == original
    code, message = server.invoke(folder, "2\nY\n\n", batch=True)
    assert code == 0 and "데이터 초기화 완료" in message
    reset_backups = _backups(folder) - backups
    assert len(reset_backups) == 1
    _assert_backup(initializer, folder, reset_backups.pop(), original[1])
    definitions, empty = server.snapshot("qa_binary")
    assert definitions == original[0] and all(not values for values in empty.values())
    code, message = server.invoke(folder, "1\nY\n\n")
    assert code == 0 and "시연 데이터 삽입 완료" in message
    assert server.assert_seed("qa_binary") == original
    backups = _backups(folder)
    server.admin("REVOKE DELETE ON `qa_binary`.* FROM %s@'127.0.0.1'", (server.username,))
    try:
        code, message = server.invoke(folder, "2\nY\n\n", batch=True)
        assert code == 1 and "DELETE" in message and "[계정·권한 확인]" in message
        assert _backups(folder) == backups  # denied before backup or deletion
        assert server.snapshot("qa_binary") == original
    finally:
        server.admin("GRANT DELETE ON `qa_binary`.* TO %s@'127.0.0.1'", (server.username,))
    wrong = secrets.token_hex(24)
    server.write_env(folder, "qa_binary", password=wrong)
    backups = _backups(folder)
    try:
        code, message = server.invoke(folder, "1\nY\n\n", extra_secrets=(wrong,))
        assert code == 1 and "1045" in message and "[계정·권한 확인]" in message
        assert _backups(folder) == backups and server.snapshot("qa_binary") == original
    finally:
        server.write_env(folder, "qa_binary")
    print("Isolated Windows frozen EXE/BAT: import/cancel/reset/restore/permission preflight/1045; 13-table structure, all rows, unrelated sentinel and credential-safe guidance verified")


def _source_program(server, initializer, name):
    folder = server.own_path(server.root / name)
    folder.mkdir()
    shutil.copyfile(server.dump, folder / "SETRON_DB.sql")
    config = server.config("qa_source")
    assert initializer.prepare(config, folder=folder, confirm=lambda _: True).applied
    return folder, config, server.assert_seed("qa_source")


def _pending_operations(folder):
    return set(folder.glob(".setron-db-*.pending.json"))


def test_windows_mysql_missing_create_privilege_does_not_create_a_database(windows_mysql, initializer, monkeypatch):
    server = windows_mysql
    folder = server.own_path(server.root / "fresh-permission-preflight")
    folder.mkdir()
    shutil.copyfile(server.dump, folder / "SETRON_DB.sql")
    config = server.config("qa_fresh")
    existing = "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s"
    assert not server.admin(existing, (config.database,))
    server.admin("REVOKE CREATE ON `qa_fresh`.* FROM %s@'127.0.0.1'", (server.username,))
    try:
        with monkeypatch.context() as isolated:
            isolated.setattr(initializer, "load_config", lambda: config)
            isolated.setattr(initializer, "program_folder", lambda: folder)
            code, message = initializer.run(confirm=lambda _: pytest.fail("Fresh privilege denial must precede confirmation"))
        assert code == 1 and "CREATE" in message and "권한" in message
        assert not server.admin(existing, (config.database,))
        assert not _backups(folder) and not _pending_operations(folder)
        server.assert_sentinel()
    finally:
        server.admin("GRANT CREATE ON `qa_fresh`.* TO %s@'127.0.0.1'", (server.username,))
    assert initializer.prepare(config, folder=folder).applied
    server.assert_seed("qa_fresh")
    print("Isolated fresh database: missing CREATE permission never provisions a partial database")


def test_windows_mysql_missing_privileges_abort_before_backup_and_changes(windows_mysql, initializer, monkeypatch):
    server = windows_mysql
    folder, config, original = _source_program(server, initializer, "permission-preflight")
    backups, pending = _backups(folder), _pending_operations(folder)
    with monkeypatch.context() as isolated:
        isolated.setattr(initializer, "load_config", lambda: config)
        isolated.setattr(initializer, "program_folder", lambda: folder)
        for privilege, mode in (
            ("CREATE", "import"), ("INSERT", "import"), ("DROP", "import"),
            ("DELETE", "reset"), ("TRIGGER", "import"), ("TRIGGER", "reset"),
        ):
            server.admin(f"REVOKE {privilege} ON `qa_source`.* FROM %s@'127.0.0.1'", (server.username,))
            confirmations = []
            try:
                code, message = initializer.run(confirm=lambda text: confirmations.append(text) or True, mode=mode)
                assert code == 1 and privilege in message and "권한" in message
                assert "[계정·권한 확인]" in message
                assert all(secret not in message for secret in (server.password, server.app_password))
                assert not confirmations, "Permission denial must precede destructive-action confirmation"
                assert _backups(folder) == backups and _pending_operations(folder) == pending
                assert server.snapshot("qa_source") == original
            finally:
                server.admin(f"GRANT {privilege} ON `qa_source`.* TO %s@'127.0.0.1'", (server.username,))
    print("Isolated privilege preflight: CREATE/INSERT/DROP/DELETE/TRIGGER denial preserves every table, row and backup")


def test_windows_mysql_existing_trigger_is_blocked_without_cross_database_effects(windows_mysql, initializer):
    server = windows_mysql
    folder, config, original = _source_program(server, initializer, "trigger-protection")
    backups, pending = _backups(folder), _pending_operations(folder)
    # This trigger would mutate the unrelated sentinel if any equipment DELETE
    # ran. The server/administrator identity is checked before both DDL calls.
    server.admin(
        "CREATE TRIGGER `qa_source`.`qa_delete_guard` BEFORE DELETE ON `qa_source`.`equipment` "
        "FOR EACH ROW UPDATE `unrelated_db`.`sentinel` SET token = 'unexpected-trigger-execution'"
    )
    try:
        for action in (initializer.prepare, initializer.reset_data):
            with pytest.raises(initializer.SetupError, match="트리거"):
                action(config, folder=folder, confirm=lambda _: pytest.fail("Trigger rejection must precede confirmation"))
            assert server.snapshot("qa_source") == original
            assert _backups(folder) == backups and _pending_operations(folder) == pending
            server.assert_sentinel()
        server.admin("REVOKE TRIGGER ON `qa_source`.* FROM %s@'127.0.0.1'", (server.username,))
        try:
            for action in (initializer.prepare, initializer.reset_data):
                with pytest.raises(initializer.SetupError, match="TRIGGER"):
                    action(config, folder=folder, confirm=lambda _: pytest.fail("Invisible metadata must not be treated as empty"))
                assert server.snapshot("qa_source") == original
                assert _backups(folder) == backups and _pending_operations(folder) == pending
                server.assert_sentinel()
        finally:
            server.admin("GRANT TRIGGER ON `qa_source`.* TO %s@'127.0.0.1'", (server.username,))
    finally:
        server.admin("DROP TRIGGER IF EXISTS `qa_source`.`qa_delete_guard`")
    assert server.assert_seed("qa_source") == original
    print("Isolated trigger protection: visible and privilege-hidden triggers never execute or change another database")


def test_windows_mysql_another_initializer_lock_blocks_all_changes(windows_mysql, initializer, monkeypatch):
    server = windows_mysql
    folder, config, original = _source_program(server, initializer, "initializer-lock")
    backups, pending = _backups(folder), _pending_operations(folder)
    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    monkeypatch.setattr(initializer, "LOCK_WAIT_SECONDS", 0)
    with closing(server.connect()) as owner:
        server.guard_connection(owner)
        with owner.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
            assert cursor.fetchone() == (1,)
            try:
                for action in (initializer.prepare, initializer.reset_data):
                    started = time.monotonic()
                    with pytest.raises(initializer.SetupError, match="실행 중"):
                        action(config, folder=folder, confirm=lambda _: pytest.fail("A concurrent tool must not request confirmation"))
                    assert time.monotonic() - started < 5
                    assert server.snapshot("qa_source") == original
                    assert _backups(folder) == backups and _pending_operations(folder) == pending
            finally:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                assert cursor.fetchone() == (1,)
    assert initializer.prepare(config, folder=folder, confirm=lambda _: True).applied
    assert server.assert_seed("qa_source") == original
    print("Isolated concurrent-tool lock: both actions reject a second owner and resume after release")


@pytest.mark.skipif(not os.environ.get("SETRON_WINDOWS_PACKAGE_DIR"), reason="Frozen Windows helper directory opt-in required")
def test_windows_mysql_frozen_missing_insert_permissions_preserve_data(windows_mysql, initializer):
    server = windows_mysql
    package = Path(os.environ["SETRON_WINDOWS_PACKAGE_DIR"]).resolve()
    folder = server.own_path(server.root / "권한 검증" / "program")
    folder.mkdir(parents=True)
    for name in ("SETRON_DB_INIT.exe", "prepare_demo_db.bat", "SETRON_DB.sql"):
        assert (package / name).is_file()
        shutil.copyfile(package / name, folder / name)
    config = server.config("qa_binary")
    assert initializer.prepare(config, folder=folder, confirm=lambda _: True).applied
    original = server.assert_seed("qa_binary")
    backups, pending = _backups(folder), _pending_operations(folder)
    server.write_env(folder, "qa_binary")
    for index, privilege in enumerate(("CREATE", "INSERT", "DROP", "TRIGGER")):
        server.admin(f"REVOKE {privilege} ON `qa_binary`.* FROM %s@'127.0.0.1'", (server.username,))
        try:
            code, message = server.invoke(folder, "1\nY\n\n", batch=bool(index % 2))
            assert code == 1 and privilege in message and "[계정·권한 확인]" in message
            assert _backups(folder) == backups and _pending_operations(folder) == pending
            assert server.snapshot("qa_binary") == original
        finally:
            server.admin(f"GRANT {privilege} ON `qa_binary`.* TO %s@'127.0.0.1'", (server.username,))
    print("Isolated frozen EXE/BAT privilege denial: CREATE/INSERT/DROP/TRIGGER rejection precedes every data change")


@pytest.mark.skipif(not os.environ.get("SETRON_WINDOWS_PACKAGE_DIR"), reason="Frozen Windows helper directory opt-in required")
def test_windows_mysql_frozen_pending_operation_blocks_restart_with_verified_backup(windows_mysql, initializer):
    """Exercise a persisted unfinished-operation record, without killing onefile workers."""
    server = windows_mysql
    package = Path(os.environ["SETRON_WINDOWS_PACKAGE_DIR"]).resolve()
    folder = server.own_path(server.root / "중단 기록 검증" / "program")
    folder.mkdir(parents=True)
    for name in ("SETRON_DB_INIT.exe", "prepare_demo_db.bat", "SETRON_DB.sql"):
        assert (package / name).is_file()
        shutil.copyfile(package / name, folder / name)
    config = server.config("qa_pending")
    assert initializer.prepare(config, folder=folder, confirm=lambda _: True).applied
    original = server.assert_seed("qa_pending")
    server.write_env(folder, "qa_pending")
    server.guard()
    with initializer._connect(config) as connection, connection.cursor() as cursor:
        connection.select_db(config.database)
        backup, _, _ = initializer._backup(connection, cursor, folder, config)
    _assert_backup(initializer, folder, backup, original[1])
    assert sum(map(len, initializer.read_dataset(backup).values())) == 9869
    # Use the production writer/format, leaving its pending status intact. This
    # tests actual next-launch behavior; abrupt process termination is not tested.
    operation = initializer.begin_operation(folder, config, "import", backup)
    record = operation.path.read_text(encoding="utf-8")
    assert all(secret not in record for secret in (server.password, server.app_password))
    backups, pending = _backups(folder), _pending_operations(folder)
    assert pending == {operation.path}
    for choice, batch in (("1", False), ("2", True)):
        code, message = server.invoke(folder, f"{choice}\nY\n\n", batch=batch)
        assert code == 1 and "이전 DB 작업" in message and "작업 기록" in message
        assert str(operation.path) in message and str(backup) in message
        assert operation.path.read_text(encoding="utf-8") == record
        assert _backups(folder) == backups and _pending_operations(folder) == pending
        assert server.snapshot("qa_pending") == original
        server.assert_sentinel()
    _assert_backup(initializer, folder, backup, original[1])
    print("Isolated frozen unfinished-operation recovery: next EXE/BAT launch stops, retains verified 9869-row backup and preserves the database; no forced termination performed")
