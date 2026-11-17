"""스키마 전용 설치의 핵심 경로만 실제 DB 없이 검증한다."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re

import pytest

from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseConfig


@pytest.fixture
def setup(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = spec_from_file_location("schema_initializer", root / "database" / "init_schema.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    source = (root / "database" / "schema_mysql5619.sql").read_text(encoding="utf-8")
    clean = "\n\n".join(re.findall(r"^CREATE TABLE [a-z_]+ \(\n.*?^\) ENGINE=.*?;[ \t]*$", source, re.M | re.S))
    statements = module.schema_statements(clean)
    config = DatabaseConfig("127.0.0.1", 3306, "setron", "app", "secret-never-print")
    return module, source, clean, statements, config


class FakeConnection:
    def __init__(self, objects=(), fail_at=None):
        self.objects, self.fail_at = objects, fail_at
        self.log, self.created = [], []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return self

    def execute(self, sql, parameters=None):
        self.log.append(sql)
        if sql.startswith("CREATE"):
            if len(self.created) == self.fail_at:
                import pymysql
                raise pymysql.OperationalError(1142, "creation denied")
            self.created.append(re.match(r"CREATE\s+TABLE\s+([a-z_]+)", sql)[1])

    def fetchone(self):
        return ("Win64",) if self.log[-1] == "SELECT @@version_compile_os" else (1,)

    def fetchall(self):
        return self.objects


def test_offline_validation_accepts_only_the_13_create_statements(setup, monkeypatch):
    module, source, clean, statements, _ = setup
    assert len(statements) == 13
    for sql in (source, clean + "\nDROP TABLE item;", clean + "\nINSERT INTO item VALUES (1);",
                clean + "\n/*! DELETE FROM item */;", clean.rstrip().removesuffix(";") + " AS SELECT 1;"):
        with pytest.raises(ValueError):
            module.schema_statements(sql)
    monkeypatch.setattr(module, "read_schema", lambda: statements)
    monkeypatch.setattr(module, "load_env_file", lambda *args: pytest.fail("offline config access"))
    monkeypatch.setattr(module.pymysql, "connect", lambda **kwargs: pytest.fail("offline DB access"))
    assert module.main(["--validate-schema"]) == 0


def test_empty_schema_creates_in_order_and_partial_ddl_failure_is_reported(setup, monkeypatch):
    module, _, _, statements, config = setup
    database = FakeConnection()
    monkeypatch.setattr(module.pymysql, "connect", lambda **kwargs: database)
    assert module.initialize(config, statements=statements, confirm=lambda target: target == config) == "created"
    assert tuple(database.created) == TABLE_NAMES
    assert all(query.startswith(("SELECT", "SHOW", "CREATE TABLE")) for query in database.log)
    database = FakeConnection(fail_at=2)
    with pytest.raises(ValueError, match="완료: 2/13개"):
        module.initialize(config, statements=statements, confirm=lambda _: True)
    assert len(database.created) == 2
    assert not any(query.startswith(("DROP", "DELETE", "INSERT", "ALTER")) for query in database.log)


@pytest.mark.parametrize("state", ["ready", "partial", "view"])
def test_existing_tables_are_protected_before_any_ddl(setup, monkeypatch, state):
    module, _, _, statements, config = setup
    objects = [(name, "BASE TABLE") for name in TABLE_NAMES]
    if state == "partial":
        objects.pop()
    if state == "view":
        objects[-1] = (objects[-1][0], "VIEW")
    database = FakeConnection(objects)
    monkeypatch.setattr(module.pymysql, "connect", lambda **kwargs: database)
    confirm = lambda _: pytest.fail("existing objects must not request creation")
    if state == "ready":
        assert module.initialize(config, statements=statements, confirm=confirm) == "ready"
    else:
        with pytest.raises(ValueError, match="변경하지 않았습니다"):
            module.initialize(config, statements=statements, confirm=confirm)
    assert not database.created


def test_cli_cancel_identifies_target_and_does_not_write(setup, monkeypatch, tmp_path, capsys):
    module, _, _, statements, config = setup
    database = FakeConnection()
    monkeypatch.setattr(module.pymysql, "connect", lambda **kwargs: database)
    monkeypatch.setattr(module, "read_schema", lambda: statements)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    env = tmp_path / "setron.env"
    env.write_text("SETRON_DB_HOST=127.0.0.1\nSETRON_DB_PORT=3306\nSETRON_DB_NAME=setron\n"
                   "SETRON_DB_USER=app\nSETRON_DB_PASSWORD=secret-never-print\n", encoding="utf-8")
    assert module.main(["--env-file", str(env)]) == 3
    output = capsys.readouterr().out
    assert "127.0.0.1:3306/setron" in output and "취소" in output
    assert config.password not in output
    assert all(query.startswith(("SELECT", "SHOW")) for query in database.log)
