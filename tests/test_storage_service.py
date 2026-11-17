"""저장 서비스 초기화에서도 공통 DB 오류 처리를 사용하는지 검증한다."""

import pytest
from sqlalchemy import create_engine

from setron.features.storage.service import StorageService
from setron.infrastructure.database import Database, DatabaseConnectionError


def test_storage_initialization_reports_driver_authentication_error():
    """저장 서비스 초기화 중 드라이버 인증 오류가 공통 접속 오류로 전달되는지 검증한다."""
    def fail_authentication():
        """DB 드라이버의 인증 준비 실패를 재현한다.

        raises
        - RuntimeError: 테스트용 인증 준비 오류를 발생시키는 경우.
        """
        raise RuntimeError("인증에 필요한 암호화 모듈을 사용할 수 없습니다.")

    database = Database(create_engine("mysql+pymysql://", creator=fail_authentication))
    try:
        with pytest.raises(DatabaseConnectionError) as caught:
            StorageService(database)
        assert isinstance(caught.value.__cause__, RuntimeError)
    finally:
        database.dispose()
