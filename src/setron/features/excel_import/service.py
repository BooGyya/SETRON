"""시트 원본 보존, AI 추출 결과 검토·수정, 저장 전 검증과 저장 요청 생성 (BE-006~BE-009).

검사 규칙(S15P31B106-39) 중 시트 안에서 확인할 수 있는 것은 여기서 검사한다.
설비 미존재·미등록 버전은 :class:`ReferenceData`를 받을 때만 확인하고, 다운그레이드처럼
이전 현황이 필요한 검사는 기준 자료 연결 전까지 안내로만 남긴다. 모르는 것을 통과로 단정하지 않는다.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from setron.features.ai.schemas import AIResult, ExtractKind, ExtractResult
from setron.features.excel_import.schemas import (
    FIELD_LABELS,
    DuplicateSaveError,
    ImportBlockedError,
    ImportDraft,
    Issue,
    IssueLevel,
    ReviewRow,
    SaveReceipt,
    SaveRequest,
    SaveRow,
    SheetSnapshot,
)
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import DatabaseCommitUnknownError, DatabaseError
from setron.infrastructure.excel_reader import SheetData, cell_address

REQUIRED = {
    ExtractKind.EQUIPMENT_STATUS: ("base_date", "site", "model_name", "equipment_code", "family_name", "version_raw"),
    ExtractKind.RELEASE_NOTE: ("version_raw",),
}
# DB 컬럼 길이. equipment_code·version_raw·pm_name은 VARBINARY 원문 기준 글자 수다.
MAX_LENGTH = {
    "site": 50, "line": 50, "group_name": 50, "model_name": 255, "equipment_code": 50,
    "pm_name": 50, "family_name": 50, "version_raw": 100,
}
DATE_FIELDS = ("base_date", "release_date")
PRODUCTION_AREAS = {"MEMORY", "FOUNDRY"}
_ADDRESS = re.compile(r"^\$?([A-Z]{1,3})\$?([1-9][0-9]*)$")
_DATE = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:[ T]00:00(?::00)?)?$")


@dataclass(frozen=True)
class ReferenceData:
    """저장된 기준 자료. 설비 호기와 SW 계열명별 등록 버전 원문이다."""

    equipment_codes: frozenset[str]
    release_versions: Mapping[str, frozenset[str]]


# --- 원본 시트 ------------------------------------------------------------

def json_value(value: Any) -> Any:
    """원본 셀 값을 JSON으로 저장할 수 있는 값으로 바꾼다. 숫자와 문자열은 그대로 둔다."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, datetime) and value.time() == datetime.min.time():
        return value.date().isoformat()  # Excel 날짜 셀은 자정 datetime으로 읽힌다.
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def cell_text(value: Any) -> str:
    """화면 표시·AI 전달·값 비교에 쓰는 셀 표기. Excel이 실수로 준 정수와 자정 날짜를 원래 표기로 돌린다."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def snapshot_sheet(sheet: SheetData) -> SheetSnapshot:
    """읽은 시트의 원본 값·주소·병합 범위를 고정한다. 이후 검토·수정으로 바뀌지 않는다."""
    cells = {
        sheet.address_of(i, j): json_value(value)
        for i, row in enumerate(sheet.values)
        for j, value in enumerate(row)
        if value is not None and cell_text(value).strip()
    }
    return SheetSnapshot(
        sheet_name=sheet.sheet_name,
        first_row=sheet.first_row,
        first_col=sheet.first_col,
        row_count=sheet.row_count,
        col_count=sheet.col_count,
        cells=cells,
        merged=tuple(m.address for m in sheet.merged_ranges),
    )


def extract_cells(snapshot: SheetSnapshot) -> list[tuple[str, str]]:
    """AI에 보낼 ``(주소, 값)`` 목록. 빈 셀은 뺀다."""
    return [(address, cell_text(value)) for address, value in snapshot.cells.items()]


def parse_address(address: str) -> tuple[int, int]:
    """``C5`` 형식 주소를 1부터 시작하는 (행, 열)로 바꾼다."""
    match = _ADDRESS.match(address.strip().upper())
    if not match:
        raise ValueError(f"셀 주소 형식이 아닙니다: {address}")
    col = 0
    for letter in match.group(1):
        col = col * 26 + ord(letter) - ord("A") + 1
    return int(match.group(2)), col


def in_sheet(snapshot: SheetSnapshot, address: str) -> bool:
    try:
        row, col = parse_address(address)
    except ValueError:
        return False
    return (snapshot.first_row <= row < snapshot.first_row + snapshot.row_count
            and snapshot.first_col <= col < snapshot.first_col + snapshot.col_count)


def value_at(snapshot: SheetSnapshot, address: str) -> Any:
    """원본 셀 값. 병합 범위 안의 칸이면 왼쪽 위 칸의 값을 돌려준다."""
    row, col = parse_address(address)
    for merged in snapshot.merged:
        (top, left), (bottom, right) = (parse_address(part) for part in merged.split(":"))
        if top <= row <= bottom and left <= col <= right:
            return snapshot.cells.get(cell_address(top, left))
    return snapshot.cells.get(cell_address(row, col))


# --- 검토 초안 ------------------------------------------------------------

def draft_from_extract(workbook_name: str, snapshot: SheetSnapshot, result: AIResult[ExtractResult]) -> ImportDraft:
    """정상 AI 결과로 검토 초안을 만든다. 실패한 결과는 초안으로 만들지 않는다."""
    if not result.ok or result.data is None:
        raise ValueError("정상 AI 추출 결과만 검토 초안으로 만들 수 있습니다.")
    extract = result.data
    if extract.sheet_name != snapshot.sheet_name:
        raise ValueError("AI 추출 결과의 시트가 읽은 시트와 다릅니다.")
    rows = tuple(
        ReviewRow(row_id=index, original=dict(row.values), sources=dict(row.sources))
        for index, row in enumerate(extract.rows, 1)
    )
    return ImportDraft(extract.kind, workbook_name, snapshot, rows, extract.review_notes, result.source)


def _replace_row(draft: ImportDraft, row: ReviewRow) -> ImportDraft:
    return replace(draft, rows=tuple(row if r.row_id == row.row_id else r for r in draft.rows))


def edit_value(draft: ImportDraft, row_id: int, name: str, text: str | None) -> ImportDraft:
    """수정값을 원본과 따로 기록한다. 빈 입력은 값 없음이며, 원본과 같아지면 수정 기록을 지운다."""
    if name not in draft.fields:
        raise ValueError(f"이 가져오기에서 쓰지 않는 항목입니다: {name}")
    row = draft.row(row_id)
    value = None if text is None or not text.strip() else text
    edits = dict(row.edits)
    original = row.original.get(name)
    if value == original or (value is None and (original is None or not original.strip())):
        edits.pop(name, None)
    else:
        edits[name] = value
    return _replace_row(draft, replace(row, edits=edits))


def revert_row(draft: ImportDraft, row_id: int) -> ImportDraft:
    return _replace_row(draft, replace(draft.row(row_id), edits={}))


def set_excluded(draft: ImportDraft, row_id: int, excluded: bool) -> ImportDraft:
    return _replace_row(draft, replace(draft.row(row_id), excluded=excluded))


# --- 검증 ---------------------------------------------------------------

def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def parse_date(text: str) -> date:
    match = _DATE.match(text.strip())
    if not match:
        raise ValueError(text)
    return date(*(int(part) for part in match.groups()))


def _normalized(row: ReviewRow, name: str) -> Any:
    """저장 값. 날짜·Item 번호·Memory/Foundry만 바꾸고 나머지 원문은 그대로 둔다."""
    value = row.value(name)
    if _blank(value):
        return None
    if name in DATE_FIELDS:
        return parse_date(value).isoformat()
    if name == "item_no":
        return int(value.strip())
    if name == "production_area":
        return value.strip().upper()
    return value


def _same_text(original: str, value: str) -> bool:
    if original == value:
        return True
    try:
        return parse_date(original) == parse_date(value)
    except ValueError:
        return False


def _source_issue(draft: ImportDraft, row: ReviewRow, name: str) -> Issue | None:
    """수정하지 않은 AI 값이 가리키는 원본 셀과 맞는지 확인한다. AI가 만든 값을 그대로 믿지 않는다."""
    value = row.original.get(name)
    if name in row.edits or _blank(value):
        return None
    address = row.sources.get(name)
    label = FIELD_LABELS[name]
    if not address:
        return Issue(IssueLevel.REVIEW, f"{label}: 원본 셀 위치가 없습니다. 원본에서 값을 확인하세요.", row.row_id, name)
    parts = address.split(":")
    if len(parts) > 2 or not all(in_sheet(draft.sheet, part) for part in parts):
        return Issue(IssueLevel.REVIEW, f"{label}: 원본 위치 {address}가 읽은 시트 범위 밖이거나 주소 형식이 아닙니다.",
                     row.row_id, name, address)
    if len(parts) == 2:
        return Issue(IssueLevel.REVIEW, f"{label}: 범위 주소라 원본 값 일치를 자동으로 확인하지 못했습니다.",
                     row.row_id, name, address)
    original = cell_text(value_at(draft.sheet, address)).strip()
    if not _same_text(original, value.strip()):
        return Issue(IssueLevel.REVIEW, f"{label}: AI 값이 원본 셀 값과 다릅니다 · 원본 {address}: {original or '빈 칸'}",
                     row.row_id, name, address)
    return None


def _row_issues(draft: ImportDraft, row: ReviewRow) -> list[Issue]:
    issues = []
    for name in draft.fields:
        value, address, label = row.value(name), row.sources.get(name), FIELD_LABELS[name]
        if name in REQUIRED[draft.kind] and _blank(value):
            issues.append(Issue(IssueLevel.ERROR, f"{label}: 필수값이 비어 있습니다.", row.row_id, name, address))
            continue
        if _blank(value):
            continue
        if name in MAX_LENGTH and len(value) > MAX_LENGTH[name]:
            issues.append(Issue(IssueLevel.ERROR, f"{label}: {MAX_LENGTH[name]}자 이내여야 합니다.", row.row_id, name, address))
        if name in ("equipment_code", "version_raw") and ("\n" in value or "\r" in value):
            issues.append(Issue(IssueLevel.ERROR, f"{label}: 원문에 줄바꿈이 있습니다. 형식 오류입니다.", row.row_id, name, address))
        if name in DATE_FIELDS:
            try:
                parse_date(value)
            except ValueError:
                issues.append(Issue(IssueLevel.ERROR, f"{label}: 날짜 형식(YYYY-MM-DD)이 아닙니다.", row.row_id, name, address))
        if name == "item_no" and not (value.strip().isascii() and value.strip().isdecimal()):
            issues.append(Issue(IssueLevel.ERROR, f"{label}: 숫자여야 합니다.", row.row_id, name, address))
        if name == "production_area" and value.strip().upper() not in PRODUCTION_AREAS:
            issues.append(Issue(IssueLevel.ERROR, f"{label}: Memory 또는 Foundry이거나 비워 두어야 합니다.",
                                row.row_id, name, address))
        if (source := _source_issue(draft, row, name)) is not None:
            issues.append(source)
    if draft.kind is ExtractKind.RELEASE_NOTE and not _blank(row.value("item_no")) and _blank(row.value("content")):
        issues.append(Issue(IssueLevel.ERROR, "변경 내용: Item 번호가 있으면 변경 내용이 필요합니다.",
                            row.row_id, "content", row.sources.get("content")))
    return issues


def _safe(row: ReviewRow, name: str) -> Any:
    try:
        return _normalized(row, name)
    except ValueError:
        return None


def _group(rows: Iterable[ReviewRow], names: tuple[str, ...]) -> dict[tuple, list[ReviewRow]]:
    groups: dict[tuple, list[ReviewRow]] = defaultdict(list)
    for row in rows:
        key = tuple(_safe(row, name) for name in names)
        if None not in key:
            groups[key].append(row)
    return groups


def _equipment_issues(rows: tuple[ReviewRow, ...]) -> list[Issue]:
    issues = []
    snapshot_fields = ("site", "line", "group_name", "model_name", "production_area")
    for (_, code), members in _group(rows, ("base_date", "equipment_code")).items():
        differing = [FIELD_LABELS[n] for n in snapshot_fields if len({_safe(r, n) for r in members}) > 1]
        if differing:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, f"같은 설비({code})·기준일인데 {', '.join(differing)} 값이 행마다 다릅니다.",
                                    row.row_id, "equipment_code", row.sources.get("equipment_code")))
    by_pm: dict[tuple, list[ReviewRow]] = defaultdict(list)
    for row in rows:
        key = tuple(_safe(row, n) for n in ("base_date", "equipment_code", "family_name"))
        if None not in key:
            by_pm[(*key, row.value("pm_name") or "")].append(row)
    by_family: dict[tuple, set] = defaultdict(set)
    for (*family_key, pm), members in by_pm.items():
        versions = {r.value("version_raw") for r in members}
        by_family[tuple(family_key)] |= versions
        pm_label = pm or "PM 구분 없음"
        if len(versions) > 1:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, f"같은 PM({pm_label})·SW 계열에 서로 다른 버전이 있습니다. 하나만 남기세요.",
                                    row.row_id, "version_raw", row.sources.get("version_raw")))
        else:
            for row in members[1:]:
                issues.append(Issue(IssueLevel.REVIEW, f"{members[0].row_id}행과 정확히 같은 행입니다. 한 행으로 합쳐 저장합니다.",
                                    row.row_id, "version_raw", row.sources.get("version_raw")))
    for family_key, versions in by_family.items():
        _, code, family = family_key
        pms = [m for k, m in by_pm.items() if k[:3] == family_key]
        if len(versions) > 1 and len(pms) > 1 and all(len({r.value("version_raw") for r in m}) == 1 for m in pms):
            for members in pms:
                issues.append(Issue(IssueLevel.INFO, f"{code}의 PM별 {family} 버전이 다릅니다. PM 불일치 예외로 표시되며 저장할 수 있습니다.",
                                    members[0].row_id, "pm_name", members[0].sources.get("pm_name")))
    return issues


def _release_issues(rows: tuple[ReviewRow, ...]) -> list[Issue]:
    issues = []
    for (version,), members in _group(rows, ("version_raw",)).items():
        dates = {_safe(r, "release_date") for r in members} - {None}
        if len(dates) > 1:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, f"같은 버전({version})의 출시일이 행마다 다릅니다.",
                                    row.row_id, "release_date", row.sources.get("release_date")))
    for (version, number), members in _group(rows, ("version_raw", "item_no")).items():
        contents = {r.value("content") for r in members}
        if len(contents) > 1:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, f"{version}의 Item {number} 내용이 행마다 다릅니다.",
                                    row.row_id, "content", row.sources.get("content")))
        else:
            for row in members[1:]:
                issues.append(Issue(IssueLevel.REVIEW, f"{members[0].row_id}행과 같은 Item입니다. 한 행으로 합쳐 저장합니다.",
                                    row.row_id, "item_no", row.sources.get("item_no")))
    return issues


def _reference_issues(draft: ImportDraft, reference: ReferenceData | None) -> list[Issue]:
    if draft.kind is ExtractKind.RELEASE_NOTE:
        return [Issue(IssueLevel.INFO, "기존 릴리즈와의 확인된 순서·교체 대상 검사는 저장 서비스 연결 후 수행합니다.")]
    issues = [Issue(IssueLevel.INFO, "버전 다운그레이드·최신보다 높은 버전 검사는 이전 현황과 확인된 순서 연결 후 수행합니다.")]
    if reference is None:
        issues.append(Issue(IssueLevel.INFO, "기준 자료 미연결 · 설비 존재와 등록 버전 여부를 확인하지 않았습니다."))
        return issues
    for row in draft.active_rows:
        code, family, version = row.value("equipment_code"), row.value("family_name"), row.value("version_raw")
        if not _blank(code) and code not in reference.equipment_codes:
            issues.append(Issue(IssueLevel.REVIEW, f"등록되지 않은 설비 호기({code})입니다. 저장하면 새 설비로 등록됩니다.",
                                row.row_id, "equipment_code", row.sources.get("equipment_code")))
        if _blank(family) or _blank(version):
            continue
        if family not in reference.release_versions:
            issues.append(Issue(IssueLevel.REVIEW, f"등록되지 않은 SW 계열({family})입니다.",
                                row.row_id, "family_name", row.sources.get("family_name")))
        elif version not in reference.release_versions[family]:
            issues.append(Issue(IssueLevel.REVIEW, "등록되지 않은 버전입니다. 원문을 보존하고 릴리즈 연결 없이 저장합니다.",
                                row.row_id, "version_raw", row.sources.get("version_raw")))
    return issues


def validate(draft: ImportDraft, reference: ReferenceData | None = None) -> tuple[Issue, ...]:
    """저장 전 검사. ERROR가 하나라도 있으면 묶음 전체를 저장하지 않는다 (HPA 포함)."""
    issues = [Issue(IssueLevel.REVIEW, note.reason, None, note.field, note.address) for note in draft.notes]
    rows = draft.active_rows
    if not rows:
        issues.append(Issue(IssueLevel.ERROR, "저장할 행이 없습니다. 제외한 행을 다시 포함하거나 다시 추출하세요."))
    for row in rows:
        issues.extend(_row_issues(draft, row))
    issues.extend(_equipment_issues(rows) if draft.kind is ExtractKind.EQUIPMENT_STATUS else _release_issues(rows))
    issues.extend(_reference_issues(draft, reference))
    order = {IssueLevel.ERROR: 0, IssueLevel.REVIEW: 1, IssueLevel.INFO: 2}
    return tuple(sorted(issues, key=lambda i: (order[i.level], i.row_id is None, i.row_id or 0)))


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(issue.level is IssueLevel.ERROR for issue in issues)


# --- 저장 요청 -------------------------------------------------------------

def build_save_request(draft: ImportDraft, request_id: str, reference: ReferenceData | None = None) -> SaveRequest:
    """검증을 통과한 초안만 저장 요청으로 만든다. 정확한 중복 행은 원본 위치를 모아 한 행으로 합친다."""
    issues = validate(draft, reference)
    if has_errors(issues):
        raise ImportBlockedError("오류가 남아 있어 저장할 수 없습니다.")
    merged: dict[tuple, SaveRow] = {}
    for row in draft.active_rows:
        values = {name: _normalized(row, name) for name in draft.fields}
        sources = {
            name: {"sheet": draft.sheet.sheet_name, "cells": address}
            for name, address in row.sources.items() if name in draft.fields
        }
        key = tuple(values[name] for name in draft.fields)
        if key in merged:
            previous = merged[key]
            combined = dict(previous.source_reference or {})
            for name, ref in sources.items():
                if name in combined:
                    cells = combined[name]["cells"].split(",")
                    if ref["cells"] not in cells:
                        combined[name] = {**combined[name], "cells": ",".join((*cells, ref["cells"]))}
                else:
                    combined[name] = ref
            merged[key] = replace(previous, row_ids=(*previous.row_ids, row.row_id), source_reference=combined or None,
                                  edited_fields=tuple(sorted({*previous.edited_fields, *row.edited_fields})))
        else:
            merged[key] = SaveRow((row.row_id,), values, sources or None, row.edited_fields)
    raw_data = {"sheet": draft.sheet.sheet_name, "cells": dict(draft.sheet.cells), "merged": list(draft.sheet.merged)}
    return SaveRequest(
        request_id=request_id,
        kind=draft.kind,
        workbook_name=draft.workbook_name,
        raw_data=raw_data,
        rows=tuple(merged.values()),
        review_count=sum(issue.level is IssueLevel.REVIEW for issue in issues),
        ai_source=draft.ai_source,
    )


class MemoryImportSaver:
    """메모리 저장소. DB 저장 서비스 연결 전 화면 흐름 확인용이며 조회 화면 자료를 바꾸지 않는다.

    ``failures``의 오류를 차례로 낸 뒤 정상 저장한다. 같은 ``request_id``는 다시 저장하지 않는다.
    """

    def __init__(self, target_label: str = "메모리 저장소 · DB 미반영", failures: Iterable[Exception] = ()) -> None:
        self.target_label = target_label
        self.failures: deque[Exception] = deque(failures)
        self.saved: list[SaveRequest] = []

    def save(self, request: SaveRequest) -> SaveReceipt:
        if any(saved.request_id == request.request_id for saved in self.saved):
            raise DuplicateSaveError("이미 저장한 요청입니다.")
        if self.failures:
            raise self.failures.popleft()
        self.saved.append(request)
        return SaveReceipt(len(self.saved), len(request.rows), datetime.now(), self.target_label)


def describe_save_error(error: Exception) -> tuple[str, str]:
    """저장 실패를 화면 상태와 안내 문구로 바꾼다. 상태는 ``unknown``·``duplicate``·``failed`` 중 하나다.

    ``unknown``은 저장 여부를 알 수 없으므로 자동 재시도하지 않는다.
    """
    if isinstance(error, DatabaseCommitUnknownError):
        return "unknown", str(error)
    if isinstance(error, DuplicateSaveError):
        return "duplicate", "이미 저장한 요청입니다. 중복 저장하지 않았습니다."
    if isinstance(error, (DatabaseError, StorageValidationError)):
        return "failed", f"{error} 이번 묶음은 저장되지 않았고 기존 자료는 그대로입니다."
    return "failed", "저장하지 못했습니다. 이번 묶음은 저장되지 않았습니다. 잠시 뒤 다시 시도하세요."
