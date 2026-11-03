"""시연용 DB 초기화 스크립트(database/reset_demo_db.py)를 실제 MySQL 5.6.19 테스트 DB에서 검증한다."""

import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from setron.features.storage.service import StorageService
from test_schema_mysql import schema_database  # noqa: F401  공통 스키마 생성·정리 fixture 재사용

pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.")

SCRIPT = Path(__file__).resolve().parents[1] / "database" / "reset_demo_db.py"
spec = importlib.util.spec_from_file_location("reset_demo_db", SCRIPT)
reset_demo_db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset_demo_db)


def scalar(database, sql):
    with database.engine.connect() as connection:
        return connection.execute(text(sql)).scalar()


def test_wrong_confirm_name_changes_nothing(schema_database, capsys):  # noqa: F811
    with StorageService(schema_database).transaction() as tx:
        tx.insert("import_batch", {"source_type": "MANUAL"})

    code = reset_demo_db.main(["--prefix", "SETRON_TEST_DB_", "--confirm", "다른_DB"])

    assert code == 2 and "아무것도 지우지 않았습니다" in capsys.readouterr().err
    assert scalar(schema_database, "SELECT COUNT(*) FROM import_batch") == 1


def test_reset_drops_data_and_applies_schema_and_seed(schema_database, capsys):  # noqa: F811
    with schema_database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE keep_me (id INT PRIMARY KEY)"))
    try:
        name = os.environ["SETRON_TEST_DB_NAME"]

        assert reset_demo_db.main(["--prefix", "SETRON_TEST_DB_", "--confirm", name]) == 0

        out = capsys.readouterr().out
        assert "초기화 완료 · 관리 테이블 13개" in out and "equipment_group 9행" in out
        assert scalar(schema_database, "SELECT COUNT(*) FROM import_batch") == 0
        assert scalar(schema_database, "SELECT COUNT(*) FROM version_family") == 9
        assert scalar(schema_database, "SELECT COUNT(*) FROM app_setting") == 1
        assert scalar(schema_database, "SELECT COUNT(*) FROM information_schema.TABLES "
                                       "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'keep_me'") == 1

        assert reset_demo_db.main(["--prefix", "SETRON_TEST_DB_", "--confirm", name, "--no-seed"]) == 0
        assert scalar(schema_database, "SELECT COUNT(*) FROM equipment_group") == 0
    finally:
        with schema_database.engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS keep_me"))
