"""MySQL 접속 설정을 읽고 업무 단위의 저장과 전체 취소를 관리한다."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


class DatabaseError(Exception):
    """DB 처리 공통 오류."""


class DatabaseConfigError(DatabaseError):
    """DB 접속 설정 오류."""


class DatabaseConnectionError(DatabaseError):
    """업무 시작 전 DB 접속 실패 오류."""


class DatabaseSaveError(DatabaseError):
    """DB 저장 처리 오류."""


class DatabaseCommitUnknownError(DatabaseSaveError):
    """최종 저장 결과 미확인 오류."""


@dataclass(frozen=True)
class DatabaseConfig:
    """MySQL 접속 설정 데이터.

    ivar
    - host: MySQL 서버 주소 또는 호스트 이름.
    - port: MySQL 접속 포트. 허용 범위는 1~65535.
    - database: 미리 생성한 DB 이름.
    - username: 앱에서 사용할 DB 계정 이름.
    - password: 공백·특수문자를 포함한 비밀번호 원문. 객체의 repr 출력에서는 제외.
    - connect_timeout: 접속 제한 시간(초). 기본값은 5.
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    connect_timeout: int = 5

    def __post_init__(self) -> None:
        """설정 객체 생성 직후 필수 문자열과 포트·접속 제한 시간을 검사한다.

        raises
        - DatabaseConfigError: 주소·DB 이름·계정이 비었거나 포트·제한 시간이 허용 범위를 벗어난 경우.
        """
        for name in ("host", "database", "username"):
            if not getattr(self, name).strip():
                raise DatabaseConfigError(f"DB 설정 {name} 값이 비어 있습니다.")
        if not 1 <= self.port <= 65535:
            raise DatabaseConfigError("DB 포트는 1~65535 사이의 정수여야 합니다.")
        if self.connect_timeout < 1:
            raise DatabaseConfigError("DB 연결 제한 시간은 1초 이상의 정수여야 합니다.")

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "SETRON_DB_",
    ) -> DatabaseConfig:
        """환경변수에서 접속 항목을 읽고 검증한 설정 객체를 생성한다.

        .env 파일을 직접 읽지는 않는다. 비밀번호의 공백·특수문자는 원문 그대로 유지한다.

        param
        - environ: 접속 항목을 읽을 환경변수 매핑. None이면 현재 프로세스의 os.environ 사용.
        - prefix: 접속 항목 이름 앞에 붙일 접두사. 기본값은 SETRON_DB_.

        return
        - 검증을 마친 DatabaseConfig. CONNECT_TIMEOUT을 생략하면 5초 적용.

        raises
        - DatabaseConfigError: 필수 환경변수가 없거나 정수 변환·접속 설정 검증에 실패한 경우.
        """
        values = os.environ if environ is None else environ
        required = ("HOST", "PORT", "NAME", "USER", "PASSWORD")
        missing = [prefix + key for key in required if prefix + key not in values]
        if missing:
            raise DatabaseConfigError("DB 접속 환경변수가 없습니다: " + ", ".join(missing))

        def integer(key: str, default: str = "") -> int:
            """접두사를 붙인 환경변수 값을 정수로 변환한다.

            param
            - key: PORT, CONNECT_TIMEOUT 등 접두사를 제외한 항목 이름.
            - default: 항목이 없을 때 변환할 기본 문자열.

            return
            - 환경변수 값 또는 기본값을 변환한 정수.

            raises
            - DatabaseConfigError: 값을 정수로 변환할 수 없는 경우.
            """
            try:
                return int(values.get(prefix + key, default))
            except (TypeError, ValueError):
                raise DatabaseConfigError(f"{prefix + key}는 정수여야 합니다.") from None

        return cls(
            host=values[prefix + "HOST"].strip(),
            port=integer("PORT"),
            database=values[prefix + "NAME"].strip(),
            username=values[prefix + "USER"].strip(),
            # 비밀번호에는 공백과 특수문자가 포함될 수 있으므로 원래 값을 유지한다.
            password=values[prefix + "PASSWORD"],
            connect_timeout=integer("CONNECT_TIMEOUT", "5"),
        )

    def to_url(self) -> URL:
        """접속 설정으로 SQLAlchemy의 MySQL 연결 URL을 생성한다.

        return
        - PyMySQL 드라이버와 utf8mb4 문자셋을 지정한 URL 객체.
        """
        return URL.create(
            "mysql+pymysql",
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
            query={"charset": "utf8mb4"},
        )


def create_mysql_engine(config: DatabaseConfig) -> Engine:
    """접속 설정으로 연결 풀과 MySQL 세션 옵션을 구성한다.

    엔진 생성 시 바로 접속하지는 않는다. 실제 접속마다 STRICT_TRANS_TABLES를 사용하고,
    SQLAlchemy 오류 출력에서 SQL 파라미터를 숨긴다.

    param
    - config: 검증한 MySQL 접속 설정.

    return
    - 연결 상태 사전 확인과 접속 제한 시간을 설정한 SQLAlchemy Engine.
    """
    return create_engine(
        config.to_url(),
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": config.connect_timeout,
            # MySQL 5.6에서도 길이를 넘는 값을 자동으로 잘라 저장하지 않도록 한다.
            "init_command": "SET SESSION sql_mode = CONCAT_WS(',', @@SESSION.sql_mode, 'STRICT_TRANS_TABLES')",
        },
        hide_parameters=True,
    )


class Database:
    """DB 연결·트랜잭션 관리자."""

    def __init__(self, engine: Engine) -> None:
        """엔진을 연결하고 업무별 세션을 생성할 팩터리를 준비한다.

        param
        - engine: 조회·저장에 사용할 SQLAlchemy Engine.
        """
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "SETRON_DB_",
    ) -> Database:
        """환경변수로 MySQL 엔진과 DB 관리자를 생성한다.

        이 시점에는 실제 서버 접속을 수행하지 않는다.

        param
        - environ: 접속 설정을 읽을 환경변수 매핑. None이면 현재 프로세스 환경 사용.
        - prefix: 접속 항목의 접두사. 테스트 DB에는 SETRON_TEST_DB_ 사용.

        return
        - 환경변수에 지정된 DB를 사용하는 Database.

        raises
        - DatabaseConfigError: 필수 설정이 없거나 설정값이 잘못된 경우.
        """
        return cls(create_mysql_engine(DatabaseConfig.from_env(environ, prefix=prefix)))

    def check_connection(self) -> None:
        """SELECT 1을 실행해 서버 접속·인증·대상 DB 접근을 확인한다.

        raises
        - DatabaseConnectionError: 연결을 확보하거나 확인 쿼리를 실행하지 못한 경우.
        """
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, RuntimeError) as exc:
            raise DatabaseConnectionError(
                "DB에 연결하지 못했습니다. MySQL 실행 상태와 접속 설정을 확인하세요."
            ) from exc

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """한 업무의 조회·저장을 같은 세션과 트랜잭션으로 관리한다.

        블록이 정상 종료하면 commit하고, 블록 안에서 오류가 나면 변경을 취소한다.
        업무 검증 오류는 원래 예외를 유지하며 종료 시 세션을 닫는다.
        최종 commit 중 연결이 끊기면 반영 여부를 단정하거나 자동 재시도하지 않는다.
        블록 안에서 commit·rollback·close를 직접 호출하지 않으며 DDL은 블록 밖에서 실행한다.

        return
        - with문 안에 SQLAlchemy Session을 전달하는 컨텍스트 관리자.

        raises
        - DatabaseConnectionError: 업무 실행 전에 DB 연결을 확보하지 못한 경우.
        - DatabaseSaveError: 업무 실행 또는 최종 저장 중 SQLAlchemy 오류가 발생한 경우.
        - DatabaseCommitUnknownError: 최종 commit 중 연결이 끊겨 저장 결과를 확인할 수 없는 경우.
        """
        connected = False
        committing = False
        try:
            with self._sessions.begin() as session:
                # 업무 실행 전에 연결을 확보해 접속 실패와 저장 실패를 구분한다.
                try:
                    session.connection()
                except RuntimeError as exc:
                    # 인증 중 드라이버가 발생시킨 RuntimeError도 접속 실패로 처리한다.
                    raise DatabaseConnectionError(
                        "DB에 연결하지 못했습니다. MySQL 실행 상태와 접속 설정을 확인하세요."
                    ) from exc
                connected = True
                yield session
                committing = True
        except SQLAlchemyError as exc:
            if not connected:
                raise DatabaseConnectionError(
                    "DB에 연결하지 못했습니다. MySQL 실행 상태와 접속 설정을 확인하세요."
                ) from exc
            if committing and isinstance(exc, DBAPIError) and exc.connection_invalidated:
                raise DatabaseCommitUnknownError(
                    "최종 저장 중 DB 연결이 끊겨 저장 결과를 확인할 수 없습니다. "
                    "자동으로 재시도하지 말고 연결 복구 후 저장된 내용을 확인하세요."
                ) from exc
            raise DatabaseSaveError(
                "데이터 저장에 실패해 이번 저장 묶음을 취소했습니다. 입력값과 DB 상태를 확인하세요."
            ) from exc

    def dispose(self) -> None:
        """연결 풀의 유휴 연결을 정리한다.

        앱 종료나 테스트 종료 시 호출한다. 현재 사용 중인 연결을 강제로 중단하지는 않는다.
        """
        self.engine.dispose()


def main() -> int:
    """기본 환경변수로 DB 접속을 확인하고 결과 안내를 출력한다.

    return
    - 연결 성공 시 0. DB 설정·접속 오류 안내를 표준 오류에 출력한 경우 1.
    """
    database = None
    try:
        database = Database.from_env()
        database.check_connection()
    except DatabaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()
    print("DB 연결을 확인했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
