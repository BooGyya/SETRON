"""프로그램 폴더의 설정으로 내장된 SETRON 시연 DB를 준비한다."""

from hashlib import sha256
import os
from pathlib import Path
import sys

import pymysql
from pymysql.constants import CLIENT

from init_local_db import execute_sql, is_loopback
from setron.features.storage.validation import TABLE_NAMES
from setron.infrastructure.database import DatabaseConfig, DatabaseConfigError
from setron.infrastructure.env_file import load_env_file


TITLE = "SETRON 시연 DB 준비"
DUMP = Path(__file__).with_name("SETRON_시연_DB_초기화.sql")


class SetupError(ValueError):
    """DB 준비 화면에 그대로 표시할 오류."""


def prepare(config: DatabaseConfig) -> bool:
    """비어 있는 DB에 덤프를 적용한다. 이미 테이블이 있는 DB이면 False를 반환한다."""
    if not is_loopback(config.host):
        raise SetupError("이 도구는 이 PC에 설치된 MySQL에서만 시연 DB를 준비합니다.")

    _, marker, dump = DUMP.read_text(encoding="utf-8").partition("\nUSE setron;\n")
    if not marker or not dump.strip():
        raise SetupError("내장 시연 데이터 파일을 읽지 못했습니다. 배포 담당자에게 문의해 주세요.")

    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    with pymysql.connect(host=config.host, port=config.port, user=config.username,
                         password=config.password, charset="utf8mb4", connect_timeout=config.connect_timeout,
                         client_flag=CLIENT.MULTI_STATEMENTS) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
        if cursor.fetchone() != (1,):
            raise SetupError("시연 DB를 준비하는 작업이 이미 실행 중입니다. 잠시 후 다시 시도해 주세요.")

        try:
            connection.select_db(config.database)
        except pymysql.err.OperationalError as error:
            if error.args[0] != 1049:
                raise
            identifier = "`" + config.database.replace("`", "``") + "`"
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {identifier} "
                           "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            connection.select_db(config.database)

        cursor.execute("SHOW FULL TABLES")
        if cursor.fetchone() is not None:
            return False

        execute_sql(cursor, dump)
        cursor.execute("SHOW TABLES")
        if {row[0] for row in cursor.fetchall()} != set(TABLE_NAMES):
            raise SetupError("테이블 준비를 완료하지 못했습니다. 배포 담당자에게 문의해 주세요.")
        connection.commit()
        return True


def load_config() -> DatabaseConfig:
    folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    path = folder / "setron.env"
    if not path.is_file():
        raise SetupError("프로그램 폴더의 DB 설정을 찾지 못했습니다.\n"
                         "이 파일을 SETRON.exe 및 setron.env와 같은 폴더에 놓고 다시 실행해 주세요.")
    values = os.environ.copy()
    load_env_file(path, values)
    return DatabaseConfig.from_env(values)


def run() -> tuple[int, str]:
    try:
        config = load_config()
        if not prepare(config):
            return 3, "대상 DB에 테이블이 있어 시연 데이터를 넣지 않았습니다. 이미 준비하셨다면 SETRON을 실행해 주세요."
    except SetupError as error:
        return 1, str(error)
    except DatabaseConfigError:
        return 1, "프로그램의 DB 연결 설정을 확인할 수 없습니다. 배포 담당자에게 문의해 주세요."
    except (OSError, UnicodeError):
        return 1, "DB 설정 또는 내장 시연 데이터 파일을 읽지 못했습니다. 배포 담당자에게 문의해 주세요."
    except pymysql.MySQLError as error:
        code = error.args[0]
        if code in (2002, 2003):
            return 1, "이 PC의 MySQL에 연결할 수 없습니다. MySQL을 시작한 뒤 다시 실행해 주세요."
        if code in (1044, 1045, 1142):
            return 1, "DB 계정 또는 권한을 확인해 주세요. DB와 테이블을 만들고 데이터를 넣을 수 있는 계정이 필요합니다."
        return 1, f"시연 DB를 준비하지 못했습니다. 배포 담당자에게 오류 코드 {code}를 알려 주세요."
    return 0, "시연 DB 준비가 끝났습니다. SETRON.exe를 실행해 주세요."


def main() -> int:
    if sys.argv[1:] == ["--quiet"]:
        return run()[0]

    import tkinter as tk
    from tkinter import messagebox

    window = tk.Tk()
    window.title(TITLE)
    window.resizable(False, False)
    tk.Label(window, text="시연 DB를 준비하는 중입니다…", padx=32, pady=24).pack()
    window.update()
    code, message = run()
    window.withdraw()
    dialog = messagebox.showinfo if code in (0, 3) else messagebox.showerror
    dialog(TITLE, message, parent=window)
    window.destroy()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
