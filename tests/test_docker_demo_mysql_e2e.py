"""Opt-in Docker E2E using only a newly created, labelled disposable container.

Set SETRON_DOCKER_E2E=1, SETRON_DOCKER_PACKAGE_DIR and
SETRON_DOCKER_DEMO_DUMP explicitly. The local setron/mysql:5.6.19 image must
already exist; this test never pulls an image or uses an existing container.
The development/test containers and ports 13306/13307 are never used.
"""
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import pymysql
import pytest

from setron.features.db_import.dump import DUMP_COLUMNS
from setron.features.storage.validation import TABLE_NAMES


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("SETRON_DOCKER_E2E") != "1"
    or not os.environ.get("SETRON_DOCKER_PACKAGE_DIR")
    or not os.environ.get("SETRON_DOCKER_DEMO_DUMP"),
    reason="Explicit disposable Docker and frozen Windows package opt-in required.",
)

_IMAGE = "setron/mysql:5.6.19"
_LABEL = "io.setron.demo.e2e"
_DATABASE = "setron_qa"
_FORBIDDEN_PORTS = {0, 3306, 13306, 13307}
_REAL_DOCKER = shutil.which("docker")


def _docker(arguments, *, env=None, input=None, timeout=30, allowed_failure=False):
    """Never print inspect JSON, environment values, or credential-bearing output."""
    try:
        assert _REAL_DOCKER, "Local Docker CLI unavailable"
        result = subprocess.run(
            [_REAL_DOCKER, *arguments], env=env, input=input, capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.fail("Disposable Docker command failed; diagnostics withheld to protect credentials")
    if result.returncode and not allowed_failure:
        pytest.fail("Disposable Docker command returned an error; diagnostics withheld")
    return result


def _normalized(rows):
    result = {}
    for table, records in rows.items():
        values = [{key: value.isoformat(sep=" ") if isinstance(value, datetime)
                   else value.isoformat() if isinstance(value, date) else value
                   for key, value in row.items()} for row in records]
        if table == "equipment_snapshot":
            for row in values:
                row.setdefault("process_name", None)
        keys = ("release_version_id", "item_id") if table == "release_version_item" else (DUMP_COLUMNS[table][0],)
        result[table] = tuple(sorted(values, key=lambda row: tuple(row[key] for key in keys)))
    return result


def _child_env(folder):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("SETRON_DB_", "SETRON_TEST_DB_", "SETRON_READ_DB_", "SETRON_AI_", "MYSQL_"))}
    env.update(TEMP=str(folder), TMP=str(folder), PYTHONIOENCODING="utf-8")
    return env


def _compile_cli_allowlist(root):
    """The tool can invoke Docker only for this sandbox, even if args regress."""
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    assert compiler.is_file(), "QA Docker allowlist compiler unavailable; refusing an unguarded EXE run"
    directory = root / "docker-allowlist"
    directory.mkdir()
    source = directory / "Proxy.cs"
    source.write_text(r'''using System;
using System.Diagnostics;
using System.IO;
using System.Text;
class Proxy {
    static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        string name = Environment.GetEnvironmentVariable("SETRON_QA_DOCKER_ALLOWED");
        string real = Environment.GetEnvironmentVariable("SETRON_QA_DOCKER_REAL");
        bool allowed = !String.IsNullOrEmpty(name) && File.Exists(real) && (
            (args.Length == 4 && args[0] == "inspect" && args[1] == "--format" && args[3] == name &&
             (args[2] == "{{.State.Running}}" || args[2] == "{{.Config.Hostname}}")) ||
            (args.Length == 3 && args[0] == "port" && args[1] == name && args[2] == "3306/tcp") ||
            (args.Length == 4 && args[0] == "exec" && args[1] == name && args[2] == "printenv" &&
             (args[3] == "MYSQL_DATABASE" || args[3] == "MYSQL_USER" || args[3] == "MYSQL_PASSWORD")));
        string trace = Environment.GetEnvironmentVariable("SETRON_QA_DOCKER_TRACE");
        File.AppendAllText(trace, (allowed ? "allow:" : "deny:") + (args.Length == 0 ? "empty" : args[0]) + "\n");
        if (!allowed) { Console.Error.Write("QA_DOCKER_DENIED"); return 97; }
        // Every allowed argument is a fixed ASCII token or the generated ASCII
        // container name; no caller-provided quoting is forwarded.
        var start = new ProcessStartInfo(real, String.Join(" ", args));
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        using (var process = Process.Start(start)) {
            string stdout = process.StandardOutput.ReadToEnd();
            string stderr = process.StandardError.ReadToEnd();
            process.WaitForExit();
            Console.Write(stdout); Console.Error.Write(stderr);
            return process.ExitCode;
        }
    }
}
''', encoding="utf-8-sig")
    result = subprocess.run([str(compiler), "/nologo", "/target:exe", "/codepage:65001",
                             "/out:" + str(directory / "docker.exe"), str(source)],
                            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, "QA Docker allowlist compilation failed; diagnostics withheld"
    return directory


@dataclass(repr=False)
class _Sandbox:
    root: Path
    name: str
    nonce: str
    hostname: str
    password: str = field(repr=False)
    app_password: str = field(repr=False)
    volumes_before: set = field(repr=False)
    identifier: str = ""
    port: int = 0
    sentinel: str = ""
    proxy_trace: Path | None = None

    def inspect(self, *, running=True, endpoint=True):
        assert re.fullmatch(r"[a-f0-9]{64}", self.identifier), "Missing owned container identity"
        result = _docker(["inspect", self.identifier], allowed_failure=not running)
        if result.returncode:
            return None
        records = json.loads(result.stdout)
        assert isinstance(records, list) and len(records) == 1
        record = records[0]
        assert record["Id"] == self.identifier and record["Name"] == "/" + self.name
        assert self.name.startswith("setron-demo-qa-") and self.name.endswith(self.nonce[:16])
        assert record["Config"]["Labels"].get(_LABEL) == self.nonce
        assert record["Config"]["Hostname"] == self.hostname
        assert not record["HostConfig"].get("Binds") and not record["HostConfig"].get("Mounts")
        for mount in record.get("Mounts", []):
            assert mount["Type"] == "volume", "Refusing an external container mount"
            assert re.fullmatch(r"[a-f0-9]{64}", mount["Name"]), "Refusing a named user volume"
            assert mount["Name"] not in self.volumes_before, "Refusing a pre-existing volume"
        if running:
            assert record["State"]["Running"] is True
        if endpoint:
            bindings = record["NetworkSettings"]["Ports"]["3306/tcp"]
            assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
            port = int(bindings[0]["HostPort"])
            assert port not in _FORBIDDEN_PORTS and 1 <= port <= 65535
            if self.port:
                assert self.port == port, "Owned QA endpoint changed"
            self.port = port
        return record

    def connect(self):
        assert self.port not in _FORBIDDEN_PORTS
        return pymysql.connect(
            host="127.0.0.1", port=self.port, user="root", password=self.password,
            charset="utf8mb4", autocommit=True, connect_timeout=1,
            read_timeout=15, write_timeout=15,
        )

    def bootstrap_tcp_administrator(self):
        """The custom image deliberately creates root@localhost only.

        Its final Unix socket appears after initialization. Create a temporary
        TCP administrator only inside the verified disposable container. SQL
        travels over stdin and synthetic credentials are never command args or
        displayed diagnostics. No production/container-default target is used.
        """
        self.inspect()
        assert re.fullmatch(r"[a-f0-9]{48}", self.password)
        sql = ("GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' IDENTIFIED BY '"
               + self.password + "' WITH GRANT OPTION;\nFLUSH HOSTS;\nSELECT 'QA_ROOT_READY';\n")
        result = _docker([
            "exec", "--interactive", self.identifier, "sh", "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --no-defaults '
            '--socket=/run/mysqld/mysqld.sock --user=root --batch --skip-column-names',
        ], input=sql.encode("ascii"), timeout=10, allowed_failure=True)
        return result.returncode == 0 and result.stdout.strip() == b"QA_ROOT_READY"

    def guard_connection(self, connection):
        self.inspect()
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@version, @@version_compile_os, @@hostname, @@port, CURRENT_USER()")
            version, platform, hostname, port, account = cursor.fetchone()
        assert version.split("-", 1)[0] == "5.6.19" and platform.lower().startswith("linux")
        assert hostname == self.hostname and port == 3306 and account.split("@", 1)[0] == "root"

    def guard(self):
        self.inspect()
        with closing(self.connect()) as connection:
            self.guard_connection(connection)

    def admin(self, sql, params=None):
        with closing(self.connect()) as connection:
            self.guard_connection(connection)
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()

    def own_path(self, path):
        path = Path(path).resolve()
        assert path != self.root and path.is_relative_to(self.root)
        return path

    def program(self, name, dump):
        folder = self.own_path(self.root / name)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dump, folder / "SETRON_DB.sql")
        return folder

    def snapshot(self):
        definitions, rows = {}, {}
        with closing(self.connect()) as connection:
            self.guard_connection(connection)
            with connection.cursor() as cursor:
                cursor.execute(f"SHOW FULL TABLES FROM `{_DATABASE}`")
                assert dict(cursor.fetchall()) == dict.fromkeys(TABLE_NAMES, "BASE TABLE")
                for table in TABLE_NAMES:
                    cursor.execute(f"SHOW CREATE TABLE `{_DATABASE}`.`{table}`")
                    definitions[table] = cursor.fetchone()[1]
                    cursor.execute(f"SELECT * FROM `{_DATABASE}`.`{table}`")
                    columns = tuple(column[0] for column in cursor.description)
                    rows[table] = tuple(dict(zip(columns, row, strict=True)) for row in cursor.fetchall())
        self.assert_sentinel()
        return definitions, _normalized(rows)

    def assert_sentinel(self):
        assert self.admin("SELECT token FROM `unrelated_qa`.`sentinel`") == ((self.sentinel,),)

    def invoke(self, folder, answers, *, batch=None):
        folder = self.own_path(folder)
        self.guard()
        if batch:
            # Fixed ASCII relative BAT name avoids cmd/CRT nested-quote ambiguity.
            args = [str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"),
                    "/d", "/v:on", "/c", ".\\" + batch, "--container", self.name]
        else:
            args = [str(folder / "SETRON_DOCKER_DB_INIT.exe"), "--container", self.name]
        assert self.proxy_trace and self.proxy_trace.is_file()
        previous_trace = self.proxy_trace.read_text(encoding="utf-8")
        before = previous_trace.count("allow:")
        result = subprocess.run(
            args, input=answers.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=folder, env=_child_env(self.root / "tmp"), timeout=150,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        combined = result.stdout + result.stderr
        if any(secret.encode() in combined for secret in (self.password, self.app_password)):
            pytest.fail("A synthetic credential appeared in tool output; diagnostics withheld")
        output = result.stdout.decode("utf-8")
        errors = result.stderr.decode("utf-8", errors="backslashreplace")
        assert not errors, f"Unexpected command error (exit={result.returncode}): {errors}"
        assert "not recognized" not in output and "'ocker'" not in output
        trace = self.proxy_trace.read_text(encoding="utf-8")
        assert trace.count("deny:") == previous_trace.count("deny:"), "Tool attempted to access a non-QA Docker target"
        if answers.startswith(("1\n", "2\n")):
            assert trace.count("allow:") > before, "Frozen tool did not use the owned-container Docker allowlist"
        self.assert_sentinel()
        return result.returncode, output

    def remove(self):
        if self.identifier and self.inspect(running=False, endpoint=False) is not None:
            _docker(["rm", "--force", "--volumes", self.identifier], timeout=60)


@pytest.fixture(scope="module")
def docker_tool():
    directory = str(Path(__file__).resolve().parents[1] / "database")
    sys.path.insert(0, directory)
    try:
        return importlib.import_module("init_docker_demo_dump")
    finally:
        sys.path.remove(directory)


@pytest.fixture(scope="module")
def docker_sandbox():
    _docker(["image", "inspect", _IMAGE])  # local only; no docker pull
    volumes = set(_docker(["volume", "ls", "--format", "{{.Name}}"]).stdout.decode().splitlines())
    with tempfile.TemporaryDirectory(prefix="setron-docker-e2e-") as temporary:
        root = Path(temporary).resolve()
        (root / "tmp").mkdir()
        proxy = _compile_cli_allowlist(root)
        nonce = uuid4().hex
        sandbox = _Sandbox(root, "setron-demo-qa-" + nonce[:16], nonce,
                           "setron-qa-" + nonce[:16], secrets.token_hex(24),
                           secrets.token_hex(24), volumes)
        creation_env = _child_env(root / "tmp")
        creation_env.update(MYSQL_DATABASE=_DATABASE, MYSQL_ROOT_PASSWORD=sandbox.password,
                            MYSQL_USER="setron_qa_app", MYSQL_PASSWORD=sandbox.app_password)
        proxy_keys = ("PATH", "SETRON_QA_DOCKER_ALLOWED", "SETRON_QA_DOCKER_REAL", "SETRON_QA_DOCKER_TRACE")
        previous_env = {key: os.environ.get(key) for key in proxy_keys}
        try:
            result = _docker([
                "create", "--name", sandbox.name, "--hostname", sandbox.hostname,
                "--label", _LABEL + "=" + nonce, "--publish", "127.0.0.1::3306",
                "--env", "MYSQL_DATABASE", "--env", "MYSQL_ROOT_PASSWORD",
                "--env", "MYSQL_USER", "--env", "MYSQL_PASSWORD", _IMAGE,
                "mysqld", "--skip-name-resolve", "--host-cache-size=0",
            ], env=creation_env)
            sandbox.identifier = result.stdout.decode().strip()
            sandbox.inspect(running=False, endpoint=False)
            _docker(["start", sandbox.identifier])
            deadline = time.monotonic() + 120
            bootstrapped = False
            while True:
                sandbox.inspect()
                if not bootstrapped:
                    bootstrapped = sandbox.bootstrap_tcp_administrator()
                    if not bootstrapped:
                        if time.monotonic() >= deadline:
                            pytest.fail("Owned Docker MySQL socket did not become ready; credentials withheld")
                        time.sleep(0.25)
                        continue
                try:
                    with closing(sandbox.connect()) as connection:
                        sandbox.guard_connection(connection)
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (_DATABASE,))
                            if cursor.fetchone() is not None:
                                break
                except pymysql.MySQLError:
                    pass
                if time.monotonic() >= deadline:
                    pytest.fail("Owned Docker MySQL did not become ready; credentials withheld")
                time.sleep(0.25)
            sandbox.admin("CREATE DATABASE `unrelated_qa`")
            sandbox.admin("CREATE TABLE `unrelated_qa`.`sentinel` (token VARCHAR(64) NOT NULL) ENGINE=InnoDB")
            sandbox.sentinel = secrets.token_hex(24)
            sandbox.admin("INSERT INTO `unrelated_qa`.`sentinel` VALUES (%s)", (sandbox.sentinel,))
            sandbox.proxy_trace = root / "docker-allowlist.trace"
            sandbox.proxy_trace.write_text("", encoding="utf-8")
            os.environ.update(PATH=str(proxy) + os.pathsep + os.environ["PATH"],
                              SETRON_QA_DOCKER_ALLOWED=sandbox.name,
                              SETRON_QA_DOCKER_REAL=str(_REAL_DOCKER),
                              SETRON_QA_DOCKER_TRACE=str(sandbox.proxy_trace))
            # Prove a default/development target is denied without forwarding
            # any command to real Docker. Helpers see this same proxy via PATH.
            denied = subprocess.run([str(proxy / "docker.exe"), "inspect", "--format", "{{.State.Running}}", "setron-mysql-dev-1"],
                                    capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            assert denied.returncode == 97 and denied.stderr == b"QA_DOCKER_DENIED"
            yield sandbox
            sandbox.assert_sentinel()
        finally:
            for key, previous in previous_env.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous
            sandbox.remove()


@pytest.fixture(scope="module")
def demo_dump():
    path = Path(os.environ["SETRON_DOCKER_DEMO_DUMP"]).resolve()
    assert path.is_file()
    return path


def _seed(sandbox, tool, folder, dump):
    sandbox.guard()
    result = tool.prepare(container=sandbox.name, folder=folder, source=dump, confirm=lambda _: True)
    assert result.applied
    snapshot = sandbox.snapshot()
    expected = _normalized(tool.shared.read_dataset(dump))
    assert snapshot[1] == expected
    assert len(expected["equipment"]) == 300 and len(expected["equipment_snapshot"]) == 1800
    assert sum(map(len, expected.values())) == 9869
    return snapshot


def _backup_matches(tool, backup, expected):
    assert backup and backup.is_file()
    assert _normalized(tool.shared.read_dataset(backup)) == expected


def test_docker_modern_process_column_backup_cancel_reset_round_trip(docker_sandbox, docker_tool, demo_dump):
    sandbox, tool = docker_sandbox, docker_tool
    folder = sandbox.program("source-modern", demo_dump)
    _seed(sandbox, tool, folder, demo_dump)
    columns = sandbox.admin("SHOW COLUMNS FROM `setron_qa`.`equipment_snapshot`")
    assert len(columns) == 14 and columns[-1][0] == "process_name"
    sandbox.admin("UPDATE `setron_qa`.`equipment_snapshot` SET process_name=%s WHERE snapshot_id=1", ("ETCH_A/ETCH_B",))
    original = sandbox.snapshot()
    backups = set((folder / "backups").glob("*.sql"))
    for action in (tool.prepare, tool.reset_data):
        sandbox.guard()
        assert not action(container=sandbox.name, folder=folder, confirm=lambda _: False).applied
        assert sandbox.snapshot() == original
        assert set((folder / "backups").glob("*.sql")) == backups
    sandbox.guard()
    reset = tool.reset_data(container=sandbox.name, folder=folder, confirm=lambda _: True)
    assert reset.applied
    _backup_matches(tool, reset.backup, original[1])
    definitions, empty = sandbox.snapshot()
    assert definitions == original[0] and all(not rows for rows in empty.values())
    _seed(sandbox, tool, folder, demo_dump)
    print("Owned Docker: 14-column process values survive verified backup; cancel/reset/reinsert and 9869 rows verified")


def test_docker_legacy_snapshot_structure_is_preserved_during_reset(docker_sandbox, docker_tool, demo_dump):
    sandbox, tool = docker_sandbox, docker_tool
    folder = sandbox.program("source-legacy", demo_dump)
    _seed(sandbox, tool, folder, demo_dump)
    assert sandbox.admin("SELECT COUNT(*) FROM `setron_qa`.`equipment_snapshot` WHERE process_name IS NOT NULL") == ((0,),)
    sandbox.admin("ALTER TABLE `setron_qa`.`equipment_snapshot` DROP COLUMN process_name")
    original = sandbox.snapshot()
    assert len(sandbox.admin("SHOW COLUMNS FROM `setron_qa`.`equipment_snapshot`")) == 13
    sandbox.guard()
    reset = tool.reset_data(container=sandbox.name, folder=folder, confirm=lambda _: True)
    assert reset.applied
    _backup_matches(tool, reset.backup, original[1])
    definitions, empty = sandbox.snapshot()
    assert definitions == original[0] and all(not rows for rows in empty.values())
    assert len(sandbox.admin("SHOW COLUMNS FROM `setron_qa`.`equipment_snapshot`")) == 13
    _seed(sandbox, tool, folder, demo_dump)
    assert len(sandbox.admin("SHOW COLUMNS FROM `setron_qa`.`equipment_snapshot`")) == 14
    print("Owned Docker: legacy 13-column reset preserves DDL and backup; modern 14-column reinsert succeeds")


def test_docker_frozen_menu_and_lf_batch_in_special_character_path(docker_sandbox, docker_tool, demo_dump):
    sandbox, tool = docker_sandbox, docker_tool
    package = Path(os.environ["SETRON_DOCKER_PACKAGE_DIR"]).resolve()
    folder = sandbox.program("한글 경로 ! & 자료", demo_dump)
    for name in ("SETRON_DOCKER_DB_INIT.exe", "prepare_docker_demo_db.bat"):
        assert (package / name).is_file()
        shutil.copyfile(package / name, folder / name)
    bat = (folder / "prepare_docker_demo_db.bat").read_bytes()
    assert bat.isascii(), "Bootstrap BAT must not contain Korean/code-page dependent menu text"
    (folder / "prepare_docker_demo_db_lf.bat").write_bytes(bat.replace(b"\r\n", b"\n"))
    _seed(sandbox, tool, folder, demo_dump)
    sandbox.admin("UPDATE `setron_qa`.`equipment_snapshot` SET process_name=%s WHERE snapshot_id=1", ("FROZEN_PROCESS",))
    original = sandbox.snapshot()
    backups = set((folder / "backups").glob("*.sql"))
    for batch in (None, "prepare_docker_demo_db.bat", "prepare_docker_demo_db_lf.bat"):
        code, text = sandbox.invoke(folder, "3\n", batch=batch)
        assert code == 0 and "SETRON Docker MySQL 데이터 관리" in text
        assert "선택 [1/2/3]: " in text
        assert sandbox.snapshot() == original
    for answers, batch in (("1\nN\n\n", "prepare_docker_demo_db.bat"), ("2\nN\n\n", None)):
        code, text = sandbox.invoke(folder, answers, batch=batch)
        assert code == 3 and "선택 [Y/N]: " in text and "취소" in text
        assert "기본 컨테이너:" not in text and "선택한 작업:" not in text
        assert "이 창에서" not in text and "입력 자료:" not in text
        assert sandbox.snapshot() == original
        assert set((folder / "backups").glob("*.sql")) == backups
    code, text = sandbox.invoke(folder, "2\nY\n\n", batch="prepare_docker_demo_db.bat")
    assert code == 0 and "초기화" in text and "완료" in text
    created = set((folder / "backups").glob("*.sql")) - backups
    assert len(created) == 1
    _backup_matches(tool, created.pop(), original[1])
    definitions, empty = sandbox.snapshot()
    assert definitions == original[0] and all(not rows for rows in empty.values())
    code, text = sandbox.invoke(folder, "1\nY\n\n")
    assert code == 0 and "완료" in text
    assert sandbox.snapshot()[1] == _normalized(tool.shared.read_dataset(demo_dump))
    print("Owned Docker frozen EXE/BAT: matching menu, LF-safe special-character path, N cancel, verified process backup, reset and reinsert")
