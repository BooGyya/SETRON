"""Docker MySQL에 시연 데이터를 넣거나 테이블을 유지하며 데이터를 초기화한다.

컨테이너의 로컬 공개 포트·환경값으로만 대상을 정하고, 기존 데이터는 확인과
SQL 백업 검증을 마친 뒤 교체한다. --validate-dump는 Docker와 DB에 접속하지 않는다.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
import re
import subprocess
import sys

import pymysql

import init_demo_dump as shared
from setron.infrastructure.database import DatabaseConfig

DEFAULT_CONTAINER = "setron-mysql-dev-1"
LINES = ("P1F", "P1D", "P4H", "P4D")


@dataclass(frozen=True)
class DockerTarget:
    container: str
    config: DatabaseConfig
    hostname: str = ""


@dataclass(frozen=True)
class DockerResult:
    applied: bool
    target: DockerTarget
    backup: Path | None = None
    summary: dict | None = None


def _docker(arguments, runner=None) -> bytes:
    """컨테이너 인증정보·오류 원문을 사용자 출력이나 예외에 넣지 않는다."""
    runner = runner or subprocess.run
    try:
        result = runner(["docker", *arguments], capture_output=True, check=False)
    except OSError:
        raise shared.SetupError("Docker CLI를 실행할 수 없습니다. Docker Desktop 설치와 실행 상태를 확인하세요.") from None
    if result.returncode:
        raise shared.SetupError("Docker 컨테이너 정보 조회에 실패했습니다. 컨테이너 이름·실행 상태·Docker 권한을 확인하세요.")
    return result.stdout.encode("utf-8") if isinstance(result.stdout, str) else result.stdout


def _published_endpoint(raw: bytes) -> tuple[str, int]:
    try:
        endpoints = []
        for line in raw.decode("utf-8").splitlines():
            host, separator, port_text = line.strip().rpartition(":")
            host = host.strip("[]")
            if not separator or not port_text.isascii() or not port_text.isdecimal():
                raise ValueError
            address = ip_address(host)
            port = int(port_text)
            if not address.is_loopback or not 1 <= port <= 65535:
                raise ValueError
            endpoints.append((str(address), port))
        if not endpoints or len({port for _, port in endpoints}) != 1:
            raise ValueError
        return min(endpoints, key=lambda item: (":" in item[0], item[0]))
    except (ValueError, UnicodeError):
        raise shared.SetupError("Docker MySQL 3306/tcp가 127.0.0.1 또는 ::1의 단일 로컬 포트로 공개되어 있어야 합니다.") from None


def docker_target(container: str = DEFAULT_CONTAINER, *, runner=None) -> DockerTarget:
    """임의 호스트나 setron.env를 사용하지 않고 Docker가 알려 준 대상만 반환한다."""
    if not isinstance(container, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
        raise shared.SetupError("Docker 컨테이너 이름이 올바르지 않습니다.")
    state = _docker(["inspect", "--format", "{{.State.Running}}", container], runner)
    if state.strip().lower() != b"true":
        raise shared.SetupError("대상 Docker 컨테이너가 실행 중이 아닙니다. 먼저 MySQL 컨테이너를 시작하세요.")
    try:
        hostname = _docker(["inspect", "--format", "{{.Config.Hostname}}", container], runner).decode("utf-8").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", hostname):
            raise ValueError
    except (UnicodeError, ValueError):
        raise shared.SetupError("컨테이너 호스트 이름을 확인하지 못했습니다.") from None
    host, port = _published_endpoint(_docker(["port", container, "3306/tcp"], runner))
    values = {}
    for field in ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"):
        raw = _docker(["exec", container, "printenv", field], runner)
        try:
            # printenv가 붙이는 마지막 LF 하나만 제거하고 비밀번호 원문은 보존한다.
            values[field] = raw.removesuffix(b"\n").decode("utf-8")
        except UnicodeError:
            raise shared.SetupError("Docker MySQL 환경값을 읽지 못했습니다. 컨테이너 설정을 확인하세요.") from None
    database, username = values["MYSQL_DATABASE"].strip(), values["MYSQL_USER"].strip()
    if not database or not username or database.casefold() in {"mysql", "sys", "information_schema", "performance_schema"}:
        raise shared.SetupError("컨테이너의 MYSQL_DATABASE와 MYSQL_USER에 시연용 DB와 앱 계정이 필요합니다.")
    return DockerTarget(container, DatabaseConfig(host, port, database, username, values["MYSQL_PASSWORD"]), hostname)


def _source_summary(rows) -> dict:
    snapshots = rows["equipment_snapshot"]
    lines = {}
    for line in LINES:
        values = [row for row in snapshots if row["site"] == "평택" and row["line"] == line]
        lines[line] = {"equipment": len({row["equipment_id"] for row in values}), "observations": len(values)}
    return {"equipment": len(rows["equipment"]), "equipment_snapshot": len(snapshots), "lines": lines}


def _query_summary(cursor) -> dict:
    result = {}
    for table in ("equipment", "equipment_snapshot"):
        cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
        result[table] = cursor.fetchone()[0]
    result["lines"] = {}
    for line in LINES:
        cursor.execute("SELECT COUNT(DISTINCT `equipment_id`), COUNT(*) FROM `equipment_snapshot` "
                       "WHERE `site` = %s AND `line` = %s", ("평택", line))
        equipment, observations = cursor.fetchone()
        result["lines"][line] = {"equipment": equipment, "observations": observations}
    return result


@contextmanager
def _database_session(target: DockerTarget):
    """검증한 Docker 서버와 지정 DB만 연결하고 두 작업이 같은 잠금을 사용한다."""
    config = target.config
    lock_name = "setron-init:" + sha256(config.database.casefold().encode()).hexdigest()[:32]
    with pymysql.connect(host=config.host, port=config.port, user=config.username,
                         password=config.password, charset="utf8mb4", connect_timeout=config.connect_timeout) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT @@version_compile_os")
        if not str(cursor.fetchone()[0]).lower().startswith("linux"):
            raise shared.SetupError("Docker의 Linux MySQL이 아닌 서버여서 중단했습니다. Windows MySQL은 변경하지 않았습니다.")
        cursor.execute("SELECT @@hostname")
        if not target.hostname or cursor.fetchone()[0] != target.hostname:
            raise shared.SetupError("연결된 MySQL이 지정한 Docker 컨테이너와 달라 중단했습니다. 다른 DB는 변경하지 않았습니다.")
        cursor.execute("SELECT GET_LOCK(%s, 5)", (lock_name,))
        if cursor.fetchone() != (1,):
            raise shared.SetupError("대상 Docker DB의 준비·초기화 작업이 이미 실행 중입니다. 잠시 후 다시 실행하세요.")
        try:
            try:
                connection.select_db(config.database)
            except pymysql.err.OperationalError as error:
                if error.args[0] != 1049:
                    raise
                raise shared.SetupError("컨테이너의 MYSQL_DATABASE에 해당하는 DB가 없습니다. 다른 DB를 만들거나 선택하지 않았습니다.") from None
            yield connection, cursor
        finally:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            except pymysql.MySQLError:
                pass


def _target_text(target: DockerTarget, folder: Path) -> str:
    config = target.config
    return (f"Docker 컨테이너: {target.container}\n"
            f"대상 DB: {config.database} · {config.host}:{config.port}\n"
            f"백업 위치: {(folder / 'backups').resolve()}")


def prepare(*, container: str = DEFAULT_CONTAINER, confirm: Callable[[str], bool] | None = None,
            folder: Path | None = None, source: Path | None = None, runner=None,
            notice: Callable[[str], None] | None = None) -> DockerResult:
    folder = Path(folder) if folder is not None else shared.program_folder()
    rows = shared.read_dataset(source or shared.dump_path(folder))
    definitions = shared.schema_statements()
    shared._check_references(rows, definitions)
    expected = _source_summary(rows)
    target = docker_target(container, runner=runner)
    config = target.config
    with _database_session(target) as (connection, cursor):
        objects = shared._objects(cursor)
        shared._check_objects(cursor, config, objects)
        backup, old_rows, old_definitions = None, None, None
        if objects:
            message = (_target_text(target, folder) + "\n\n"
                       "기존 데이터가 있습니다. 시연 데이터로 덮어쓰시겠습니까?\n")
            if confirm is None or not confirm(message):
                return DockerResult(False, target)
            try:
                backup, old_rows, old_definitions = shared._backup(connection, cursor, folder, config)
            except Exception:
                raise shared.SetupError("Docker DB의 SQL 백업·검증에 실패했습니다. 기존 테이블과 데이터는 변경하지 않았습니다.") from None
        elif notice is not None:
            notice(_target_text(target, folder))
        try:
            if objects:
                shared._drop(cursor)
            shared._apply(connection, cursor, rows, definitions)
            summary = _query_summary(cursor)
            if summary != expected:
                raise shared.SetupError("설비·관측·라인별 행 수가 SQL 자료와 다릅니다.")
        except Exception:
            try:
                connection.rollback()
                shared._drop(cursor)
                if old_rows is not None:
                    shared._apply(connection, cursor, old_rows, old_definitions)
                else:
                    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
                restored = "기존 데이터를 자동 복원했습니다." if backup else "이번에 만든 관리 테이블을 정리했습니다."
            except Exception:
                restored = "자동 복원을 완료하지 못했습니다. 백업 SQL로 복원해 주세요." if backup else "대상 Docker DB의 관리 테이블 상태를 확인해 주세요."
            suffix = f"\n기존 DB 백업: {backup}" if backup else ""
            raise shared.SetupError(f"Docker 시연 데이터 적용에 실패했습니다. {restored}{suffix}") from None
        return DockerResult(True, target, backup, summary)


def reset_data(*, container: str = DEFAULT_CONTAINER, confirm: Callable[[str], bool] | None = None,
               folder: Path | None = None, runner=None) -> DockerResult:
    """Docker의 관리 테이블 13개 구조를 유지하고 확인·백업 후 모든 행을 삭제한다."""
    folder = Path(folder) if folder is not None else shared.program_folder()
    shared.schema_statements()
    target = docker_target(container, runner=runner)
    with _database_session(target) as (connection, cursor):
        objects = shared._objects(cursor)
        shared._check_objects(cursor, target.config, objects)
        if set(objects) != set(shared.TABLE_NAMES):
            raise shared.SetupError("초기화할 관리 테이블 13개가 없습니다. 먼저 1번 '시연 데이터 넣기'를 실행하세요.")
        shared._check_reset_tables(cursor, target.config)
        message = (_target_text(target, folder) + "\n\n"
                   "테이블 구조를 유지하고 모든 데이터를 삭제하시겠습니까?\n")
        if confirm is None or not confirm(message):
            return DockerResult(False, target)
        try:
            backup, _, _ = shared._backup(connection, cursor, folder, target.config)
        except Exception:
            raise shared.SetupError("Docker DB의 SQL 백업·검증에 실패했습니다. 기존 테이블과 데이터는 변경하지 않았습니다.") from None
        try:
            shared._reset_rows(connection, cursor)
        except Exception:
            raise shared.SetupError("Docker 데이터 초기화에 실패했습니다. 삭제 트랜잭션의 취소를 요청했습니다.\n"
                                    f"기존 DB 백업: {backup}") from None
        return DockerResult(True, target, backup, {"tables": len(shared.TABLE_NAMES), "rows": 0})


def _format_summary(summary):
    lines = [f"설비 {summary['equipment']}대 · 관측 {summary['equipment_snapshot']}개"]
    for line, count in summary["lines"].items():
        lines.append(f"평택 {line}: {count['equipment']}대 · {count['observations']}개 관측")
    return "\n".join(lines)


def run(*, container=DEFAULT_CONTAINER, confirm=None, mode="import", notice=None):
    try:
        if mode not in {"import", "reset"}:
            return 2, "지원하지 않는 Docker DB 작업입니다."
        options = {"container": container, "confirm": confirm}
        if mode == "import" and notice is not None:
            options["notice"] = notice
        result = (reset_data if mode == "reset" else prepare)(**options)
        if not result.applied:
            return 3, "취소했습니다. DB는 변경하지 않았습니다."
        message = "데이터 초기화 완료" if mode == "reset" else "시연 데이터 넣기 완료"
        if result.backup:
            message += f"\n백업 파일: {result.backup}"
        return 0, message
    except (shared.SetupError, shared.DumpFormatError) as error:
        return 1, str(error)
    except pymysql.MySQLError as error:
        code = error.args[0] if error.args else "확인 불가"
        return 1, f"Docker MySQL 연결·권한을 확인하세요. 오류 코드: {code}"
    except (OSError, UnicodeError, ValueError):
        return 1, "SQL·관리 스키마·Docker 설정을 읽거나 검증하지 못했습니다. Docker 연결 상태와 배포 파일을 확인하세요."
    except Exception:
        return 1, "Docker DB 작업을 중단했습니다. 배포 파일과 컨테이너 상태를 확인하세요."


def _console_notice(message):
    print("\n" + message + "\n")


def _console_menu(*, container: str, confirm: Callable[[str], bool]) -> int:
    print("SETRON Docker MySQL 데이터 관리")
    print("1. 시연 데이터 넣기 - 기존 자료 교체")
    print("2. 데이터 초기화 - 테이블 구조 유지, 모든 데이터 삭제")
    print("3. 종료\n")
    while True:
        try:
            choice = input("선택 [1/2/3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            choice = "3"
        if choice == "3":
            print("종료합니다.")
            return 0
        if choice in {"1", "2"}:
            break
        print("1, 2, 3 중 하나를 입력하세요.")
    code, message = run(container=container, confirm=confirm,
                        mode="import" if choice == "1" else "reset", notice=_console_notice)
    print(message)
    try:
        input("Enter 키를 누르면 창을 닫습니다: ")
    except (EOFError, KeyboardInterrupt):
        print()
    return code


def main(argv=None) -> int:
    # Windows 콘솔의 기본 cp949 때문에 한국어·경로가 손상되지 않게 한다.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--import-demo", action="store_true", help="관리 테이블을 시연 구조와 데이터로 교체")
    action.add_argument("--reset-data", action="store_true", help="관리 테이블 구조를 유지하고 모든 데이터 삭제")
    action.add_argument("--validate-dump", action="store_true", help="Docker와 DB에 접속하지 않고 SQL만 검증")
    parser.add_argument("--container", default=DEFAULT_CONTAINER, help="이미 실행 중인 MySQL 컨테이너 이름")
    parser.add_argument("--quiet", action="store_true", help="확인 질문 없이 실행. 기존 DB가 있으면 취소")
    args = parser.parse_args(argv)
    if args.validate_dump:
        try:
            rows = shared.read_dataset(shared.dump_path(shared.program_folder()))
            shared._check_references(rows, shared.schema_statements())
            print("SQL 검증 완료 · 관리 테이블 13개\n" + _format_summary(_source_summary(rows)))
            print("Docker와 DB에는 접속하지 않았습니다.")
            return 0
        except (OSError, UnicodeError, ValueError):
            print("SQL 검증에 실패했습니다. Docker와 DB에는 접속하지 않았습니다.")
            return 1

    def confirm(message):
        print("\n" + message)
        if sys.platform == "win32" and sys.stdin is not None and sys.stdin.isatty():
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        try:
            return input("선택 [Y/N]: ").strip().casefold() in {"y", "yes", "예"}
        except (EOFError, KeyboardInterrupt):
            return False

    if not (args.import_demo or args.reset_data or args.quiet):
        return _console_menu(container=args.container, confirm=confirm)
    code, message = run(container=args.container, confirm=None if args.quiet else confirm,
                        mode="reset" if args.reset_data else "import", notice=None if args.quiet else _console_notice)
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
