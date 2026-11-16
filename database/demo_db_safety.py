"""DB 변경 전에 권한과 미완료 작업을 확인한다. 인증 정보는 저장하지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import uuid4


class SafetyError(ValueError):
    """사용자에게 그대로 표시해도 되는 안전한 안내."""


class CommitOutcomeUnknown(SafetyError):
    """COMMIT 응답이 없어 DB 반영 여부를 확정할 수 없다."""


_RIGHTS = {
    "import": frozenset({"SELECT", "CREATE", "DROP", "INSERT", "REFERENCES", "TRIGGER"}),
    "reset": frozenset({"SELECT", "DELETE", "TRIGGER"}),
}
_IDENTIFIER = r"(?:`(?:``|[^`])+`|[^.\s]+)"
_OBJECT = re.compile(rf"^({_IDENTIFIER})\.({_IDENTIFIER})$")


def _identifier(value):
    return value[1:-1].replace("``", "`") if value.startswith("`") and value.endswith("`") else value


def _database_matches(pattern, database):
    # MySQL DB 수준 GRANT의 %/_는 wildcard이고 \%/\_는 문자 그대로다.
    expression, escaped = [], False
    for char in pattern:
        if escaped:
            expression.append(re.escape(char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "%":
            expression.append(".*")
        elif char == "_":
            expression.append(".")
        else:
            expression.append(re.escape(char))
    if escaped:
        expression.append(re.escape("\\"))
    return re.fullmatch("".join(expression), database, re.I) is not None


def check_privileges(cursor, database, mode, tables, *, create_database=False):
    """현재 계정의 전체 테이블 권한을 검사하며 컬럼 한정 권한은 인정하지 않는다."""
    required = _RIGHTS[mode]
    permitted = {table: set() for table in tables}
    global_rights = set()
    database_grants = {}
    cursor.execute("SHOW GRANTS FOR CURRENT_USER")
    grants = cursor.fetchall()
    for record in grants:
        if not record or not isinstance(record[0], str):
            raise SafetyError("현재 계정의 권한 형식을 확인할 수 없어 중단했습니다. DB를 변경하지 않았습니다.")
        # TO 이후에는 암호 hash 등이 있을 수 있으므로 해석하거나 출력하지 않는다.
        match = re.match(r"^GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s", record[0], re.I)
        if match is None:
            raise SafetyError("현재 계정의 권한 형식을 지원하지 않아 중단했습니다. 관리자에게 권한 확인을 요청하세요. DB를 변경하지 않았습니다.")
        scope = _OBJECT.fullmatch(match[2].strip())
        if scope is None:
            # Stored routine/proxy grants cannot confer table privileges.
            if re.match(r"^(?:PROCEDURE|FUNCTION)\s", match[2], re.I) or match[1].upper() == "PROXY":
                continue
            raise SafetyError("현재 계정의 권한 범위를 확인할 수 없어 중단했습니다. DB를 변경하지 않았습니다.")
        db_scope, table_scope = (_identifier(value) for value in scope.groups())
        global_scope = db_scope == "*" and table_scope == "*"
        # Database wildcards apply only to db.* grants; db.table uses literal DB names.
        matches = (_database_matches(db_scope, database) if table_scope == "*"
                   else db_scope.casefold() == database.casefold())
        if not global_scope and not matches:
            continue
        # Parenthesized column lists may contain commas, but cannot grant table-wide rights.
        chunks = re.split(r",(?![^()]*\))", match[1].upper())
        rights = {value.strip() for value in chunks if re.fullmatch(r"[A-Z ]+", value.strip())}
        if "ALL PRIVILEGES" in rights or "ALL" in rights:
            rights |= _RIGHTS["import"] | _RIGHTS["reset"]
        if global_scope:
            global_rights.update(rights)
        elif table_scope == "*":
            database_grants.setdefault(db_scope, set()).update(rights)
        elif table_scope in permitted:
            permitted[table_scope].update(rights)
    # MySQL chooses the first matching DB grant rather than unioning wildcard
    # patterns. Intersection is conservative when SHOW GRANTS omits that order.
    database_rights = set.intersection(*database_grants.values()) if database_grants else set()
    database_rights |= global_rights
    for rights in permitted.values():
        rights.update(database_rights)
    missing = set().union(*(required - rights for rights in permitted.values()))
    if create_database and "CREATE" not in database_rights:
        missing.add("CREATE (DB 생성)")
    if missing:
        raise SafetyError(
            "필수 MySQL 권한이 부족하거나 확인되지 않아 중단했습니다. DB를 변경하지 않았습니다.\n"
            f"부족한 권한: {', '.join(sorted(missing))}\n"
            "관리자에게 대상 DB에 한정한 권한 설정을 요청하세요."
        )


def _target(config):
    # 도구는 로컬 서버만 사용하므로 localhost/127.0.0.1/::1은 같은 대상으로 묶는다.
    return {"port": config.port, "database": config.database.casefold()}


def journal_path(folder, config):
    fingerprint = sha256(json.dumps(_target(config), sort_keys=True).encode()).hexdigest()[:20]
    return Path(folder) / f".setron-db-{fingerprint}.pending.json"


def recovery_message(path, record=None):
    record = record or {}
    backup = record.get("backup")
    return ("이전 DB 작업의 완료 여부를 확인할 수 없어 중단했습니다.\n"
            f"작업 기록: {path}\n"
            + (f"백업: {backup}\n" if isinstance(backup, str) and backup else "백업: 최초 삽입 작업은 기존 DB 백업이 없을 수 있습니다.\n")
            + "앱과 다른 DB 도구를 닫고 관리자에게 대상 DB 확인·복구를 요청하세요.\n"
            "DB 복구 또는 테이블·데이터 확인을 마친 뒤에만 위 작업 기록 파일을 삭제하고 다시 실행하세요.")


def assert_no_pending(folder, config):
    path = journal_path(folder, config)
    if not path.exists():
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        valid = (isinstance(record, dict) and record.get("format") == 1
                 and record.get("target") == _target(config))
    except (OSError, UnicodeError, ValueError):
        record, valid = {}, False
    if not valid or record.get("status") != "completed":
        raise SafetyError(recovery_message(path, record if valid else None))


@dataclass
class OperationJournal:
    path: Path
    record: dict

    def mark(self, status):
        self.record["status"] = status
        temporary = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(self.record, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def complete(self):
        # 완료 기록을 먼저 영속화한다. unlink 실패가 DB 작업 실패로 바뀌지 않게 한다.
        try:
            self.mark("completed")
            self.path.unlink()
        except OSError:
            return f"작업은 완료했으나 작업 기록을 정리하지 못했습니다: {self.path}"
        return ""

    def uncertain(self):
        try:
            self.mark("outcome_unknown")
        except OSError:
            pass  # 기존 pending 기록도 다음 실행을 안전하게 중단한다.


def begin_operation(folder, config, mode, backup):
    assert_no_pending(folder, config)
    path = journal_path(folder, config)
    if path.exists():  # 완료했지만 이전 unlink가 실패한 경우: 새 변경 전에만 정리한다.
        try:
            path.unlink()
        except OSError as exc:
            raise SafetyError(f"완료된 작업 기록을 정리할 수 없어 중단했습니다. DB를 변경하지 않았습니다.\n작업 기록: {path}") from exc
    record = {"format": 1, "target": _target(config), "mode": mode, "status": "pending",
              "started_at": datetime.now(timezone.utc).isoformat(),
              "backup": str(Path(backup).resolve()) if backup is not None else None}
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise SafetyError("작업 기록을 저장하지 못해 중단했습니다. DB를 변경하지 않았습니다.\n"
                          f"프로그램 폴더의 쓰기 권한과 디스크 공간을 확인하세요: {path}") from exc
    return OperationJournal(path, record)
