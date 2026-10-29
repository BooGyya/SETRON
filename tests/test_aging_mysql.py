"""실제 MySQL 5.6.19에서 노후도 기간 기준을 저장하고 다시 읽는지 검증한다 (BE-016)."""

import os
from pathlib import Path

import pymysql
import pytest
from pymysql.constants import CLIENT
from sqlalchemy import MetaData, inspect

from setron.features.aging.repository import AgingSettingsRepository
from setron.features.aging.schemas import DEFAULT_THRESHOLDS, AgeThresholds, AgeUnit, ThresholdError
from setron.features.storage.service import StorageService
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import Database, DatabaseConfig

pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"),
    reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


@pytest.fixture(scope="module")
def storage():
    """테스트 DB에 공통 스키마를 적용하고, 끝나면 이번에 만든 테이블만 지운다 (test_schema_mysql과 같은 방식)."""
    config = DatabaseConfig.from_env(prefix="SETRON_TEST_DB_")
    database = Database.from_env(prefix="SETRON_TEST_DB_")
    schema_path = Path(__file__).resolve().parents[1] / "database" / "schema_mysql5619.sql"
    before = set(inspect(database.engine).get_table_names())
    assert not before.intersection(TABLE_NAMES), "기존 관리 테이블이 있는 DB에서는 실행하지 않습니다."
    try:
        connection = pymysql.connect(
            host=config.host, port=config.port, user=config.username, password=config.password,
            database=config.database, charset="utf8mb4", client_flag=CLIENT.MULTI_STATEMENTS,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(schema_path.read_text(encoding="utf-8"))
                while cursor.nextset():
                    pass
            connection.commit()
        finally:
            connection.close()
        yield StorageService(database)
    finally:
        created = set(inspect(database.engine).get_table_names()).intersection(TABLE_NAMES) - before
        if created:
            metadata = MetaData()
            metadata.reflect(bind=database.engine, only=created)
            metadata.drop_all(database.engine)
        database.dispose()


def test_schema_default_is_loaded_as_one_and_two_years(storage):
    loaded = AgingSettingsRepository(storage).load()

    assert loaded.thresholds == DEFAULT_THRESHOLDS
    assert not loaded.is_default  # 스키마가 넣은 기본 행을 읽음


def test_saved_thresholds_survive_reload_and_invalid_ones_are_not_saved(storage):
    repository = AgingSettingsRepository(storage)

    repository.save({"warning": 6, "danger": 18, "unit": "MONTH"})
    with pytest.raises(ThresholdError):
        repository.save({"warning": -1, "danger": 18, "unit": "MONTH"})

    assert repository.load().thresholds == AgeThresholds(6, 18, AgeUnit.MONTH)
