"""저장 서비스 초기화에서도 공통 DB 오류 처리를 사용하는지 검증한다."""

import pytest
from sqlalchemy import create_engine

from setron.features.storage.service import StorageService
from setron.infrastructure.database import Database, DatabaseConnectionError


def test_storage_initialization_reports_driver_authentication_error():
    def fail_authentication():
        raise RuntimeError("인증에 필요한 암호화 모듈을 사용할 수 없습니다.")

    database = Database(create_engine("mysql+pymysql://", creator=fail_authentication))
    try:
        with pytest.raises(DatabaseConnectionError) as caught:
            StorageService(database)
        assert isinstance(caught.value.__cause__, RuntimeError)
    finally:
        database.dispose()
