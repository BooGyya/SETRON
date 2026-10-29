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
    """화면과 실행 진입점에서 공통으로 처리할 DB 오류."""


class DatabaseConfigError(DatabaseError):
    """DB 접속 설정이 없거나 형식이 올바르지 않을 때 발생한다."""


class DatabaseConnectionError(DatabaseError):
    """업무를 시작하기 전에 DB 연결에 실패했을 때 발생한다."""


class DatabaseSaveError(DatabaseError):
    """DB에 데이터를 저장하는 중 오류가 발생했음을 나타낸다."""


class DatabaseCommitUnknownError(DatabaseSaveError):
    """최종 저장 중 연결이 끊겨 저장 여부를 확인할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    connect_timeout: int = 5

    def __post_init__(self) -> None:
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
        """환경변수에서 DB 접속 설정을 읽는다.

        .env 파일은 실행 명령에서 불러오며, 대상 DB는 미리 준비해야 한다.
        """
        values = os.environ if environ is None else environ
        required = ("HOST", "PORT", "NAME", "USER", "PASSWORD")
        missing = [prefix + key for key in required if prefix + key not in values]
        if missing:
            raise DatabaseConfigError("DB 접속 환경변수가 없습니다: " + ", ".join(missing))

        def integer(key: str, default: str = "") -> int:
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
    """엔진을 구성한다. 실제 접속은 연결을 처음 사용할 때 이루어진다."""
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
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "SETRON_DB_",
    ) -> Database:
        return cls(create_mysql_engine(DatabaseConfig.from_env(environ, prefix=prefix)))

    def check_connection(self) -> None:
        """SELECT 1을 실행해 접속과 인증, 대상 DB에 대한 접근을 확인한다."""
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, RuntimeError) as exc:
            raise DatabaseConnectionError(
                "DB에 연결하지 못했습니다. MySQL 실행 상태와 접속 설정을 확인하세요."
            ) from exc

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """블록이 정상 종료하면 한 번에 저장하고, 예외가 밖으로 전달되면 전체 취소한다.

        작업이 끝나면 성공 여부와 관계없이 세션을 닫는다.
        서비스는 하나의 저장 업무를 이 블록으로 감싸고 모든 Repository에 같은 세션을 전달한다.
        블록 안에서는 commit(), rollback(), close()를 직접 호출하지 않는다.
        MySQL 테이블에는 InnoDB를 사용하고, 테이블 생성·변경 등의 DDL은 블록 밖에서 실행한다.
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
        """앱 종료 시 연결 풀에 남아 있는 유휴 연결을 정리한다."""
        self.engine.dispose()


def main() -> int:
    """앱 화면을 실행하지 않고 DB 접속을 확인한다."""
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
