"""시트 원본 보존, AI 추출 결과 검토·수정, 저장 전 검증과 저장 요청 생성 (BE-006~BE-009).

검사 규칙(S15P31B106-39) 중 시트 안에서 확인할 수 있는 것은 여기서 검사한다.
기존값·릴리즈 등록·N 비교는 :class:`ReferenceData`를 받아 확인한다. 핫픽스 접미사의
모양이나 숫자로 순서를 추정하지 않으며 미등록·순서 미확인만으로 저장을 차단하지 않는다.
다운그레이드 경고 정책은 확정 범위에서 제외하고 같은 날짜의 기존값 변경만 안내한다.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import replace
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from setron.features.ai.schemas import AIResult, ExtractKind, ExtractResult
from setron.features.excel_import.schemas import (
    FIELD_LABELS,
    FIELDS,
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
from setron.features.storage.inputs import merge_sources
from setron.features.version.service import compare_version
from setron.features.extraction.schemas import ExtractionReview
from setron.features.extraction.normalization import normalize
from setron.features.extraction.locations import record_locations
from setron.infrastructure.ai_client import ResponseSource
from .reference import ReferenceData, analysis_excluded, family_key, identity, model_issue, resolve_family
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
    "ui_type": 20, "structure_type": 50, "geometry_type": 50, "modification_type": 100,
}
DATE_FIELDS = ("base_date", "release_date")
CONTEXT_FIELDS = ("group_name", "model_name", "family_name", "ui_type", "structure_type")
PRODUCTION_AREAS = {"MEMORY", "FOUNDRY"}
SOURCE_REQUIRED_FIELDS = {"equipment_code", "version_raw"}
_ADDRESS = re.compile(r"^\$?([A-Z]{1,3})\$?([1-9][0-9]*)$")
_DATE = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:[ T]00:00(?::00)?)?$")


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
    return [(address, cell_text(value)) for address, value in snapshot.cells.items()
            if value is not None and cell_text(value).strip()]


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

def draft_from_extract(workbook_name: str, snapshot: SheetSnapshot, result: AIResult[ExtractResult],
                       *, workbook_id: str | None = None) -> ImportDraft:
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
    return ImportDraft(extract.kind, workbook_name, snapshot, rows, extract.review_notes, result.source,
                       workbook_id=workbook_id)


def draft_from_review(review: ExtractionReview, *, source: ResponseSource | None = None,
                      workbook_id: str | None = None) -> ImportDraft:
    """BE-007의 원본 대조·정규화 결과를 수정·재검증할 초안으로 연결한다.

    param
    - review: 원본 셀·AI 추출 원문·확정 정규값을 가진 BE-007 결과.
    - source: AI 응답 출처. 미지정이면 출처 미확인 상태를 유지.

    return
    - 원본과 정규값을 따로 보존한 초안. 행별 추출 오류는 차단하고 전체 확인 메모는 따로 안내.

    raises
    - ValueError: 원본 시트의 셀 자료가 없거나 주소가 잘못된 경우.
    """
    cells = deepcopy(review.raw_data.get(review.sheet_name))
    if not isinstance(cells, dict):
        raise ValueError("추출 결과의 원본 시트 자료가 없습니다.")
    positions = [parse_address(address) for address in cells]
    top = min((row for row, _ in positions), default=1)
    left = min((col for _, col in positions), default=1)
    snapshot = SheetSnapshot(review.sheet_name, top, left,
        max((row for row, _ in positions), default=top) - top + 1,
        max((col for _, col in positions), default=left) - left + 1, cells)
    rows = tuple(ReviewRow(index, deepcopy(row.raw_values),
                          {field: cell.address for field, cell in row.sources.items()}, normalized=deepcopy(row.values))
                 for index, row in enumerate(review.rows, 1))
    notes = tuple(Issue(IssueLevel.ERROR if note.row_number is not None or getattr(note, "blocks_save", False)
                        else IssueLevel.REVIEW,
                        note.reason, note.row_number, note.field, note.address,
                        code=getattr(note, "code", None) or "EXTRACTION_UNRESOLVED") for note in review.issues)
    return ImportDraft(review.kind, review.workbook_name, snapshot, rows, ai_source=source, extraction_issues=notes,
                       workbook_id=workbook_id)


def _replace_row(draft: ImportDraft, row: ReviewRow) -> ImportDraft:
    return replace(draft, rows=tuple(row if r.row_id == row.row_id else r for r in draft.rows))


def edit_value(draft: ImportDraft, row_id: int, name: str, text: str | None) -> ImportDraft:
    """수정값을 원본과 따로 기록하고 미해결 추출값의 명시적인 공란 수정도 보존한다.

    param
    - draft, row_id: 수정할 초안과 추출 행 ID.
    - name, text: 공통 항목과 수정 문자열. None·공백은 값 없음.

    return
    - 원본·이전 초안을 변경하지 않은 새 초안.

    raises
    - ValueError: 자료 종류에 없는 항목을 수정한 경우.
    - LookupError: 추출 행 ID가 없는 경우.
    """
    if name not in draft.fields:
        raise ValueError(f"이 가져오기에서 쓰지 않는 항목입니다: {name}")
    row = draft.row(row_id)
    value = None if text is None or not text.strip() else text
    edits = dict(row.edits)
    original = row.normalized.get(name, row.original.get(name))
    # 자동 정리된 None과 사용자가 직접 비운 결과는 값이 같아도 검토 상태가 다르다.
    cleared_extraction_error = value is None and row.normalized.get(name) is None and any(
        issue.row_id == row_id and issue.field == name and issue.blocks_save
        for issue in draft.extraction_issues
    )
    same_as_original = value == original or (value is None and (original is None or not original.strip()))
    if same_as_original and not cleared_extraction_error:
        edits.pop(name, None)
    else:
        edits[name] = value
    from .review_locations import refresh_hpa_coverage

    return refresh_hpa_coverage(_replace_row(draft, replace(row, edits=edits)))


def revert_row(draft: ImportDraft, row_id: int) -> ImportDraft:
    from .review_locations import refresh_hpa_coverage

    return refresh_hpa_coverage(_replace_row(draft, replace(draft.row(row_id), edits={})))


def set_excluded(draft: ImportDraft, row_id: int, excluded: bool) -> ImportDraft:
    from .review_locations import refresh_hpa_coverage

    return refresh_hpa_coverage(_replace_row(draft, replace(draft.row(row_id), excluded=excluded)))


# --- 검증 ---------------------------------------------------------------

def _blank(value: str | None) -> bool:
    """누락된 문자열 입력인지 확인한다.

    param
    - value: 검토할 현재 값. 문자열이 아닌 값은 누락과 별도로 형식 오류로 처리.

    return
    - None 또는 공백 문자열이면 True.
    """
    return value is None or isinstance(value, str) and not value.strip()


def parse_date(text: str) -> date:
    match = _DATE.match(text.strip())
    if not match:
        raise ValueError(text)
    return date(*(int(part) for part in match.groups()))


def _normalized_context(row: ReviewRow) -> dict:
    """서로 의존하는 모델·계열·UI·구조를 한 번에 정리한다.

    param
    - row: 현재 수정값을 가진 검토 행.

    return
    - 계열명에서 확인한 C3 UI 등 공란 항목의 확정 정보도 포함한 공통 항목 사전.
      자료형이 잘못된 값은 원래 형태로 남겨 항목별 검증에서 처리.
    """
    context = {field: row.value(field) for field in CONTEXT_FIELDS}
    if all(value is None or isinstance(value, str) for value in context.values()):
        return normalize(context, ExtractKind.EQUIPMENT_STATUS, row.row_id, {})[0]
    return context


def _normalized(row: ReviewRow, name: str, *, context: dict | None = None) -> Any:
    """확정된 공통 항목만 정규화하며 SW·호기 원문은 변경하지 않는다.

    param
    - row, name: 현재 검토 행과 조회할 공통 항목.
    - context: 같은 행에서 함께 계산한 모델·계열 정규값. None이면 현재 행에서 계산.

    return
    - 날짜·Item 번호·분류 등의 정규값. 값이 없으면 None.

    raises
    - ValueError: 문자열이 아니거나 날짜·번호 형식이 잘못된 경우.
    """
    if name in CONTEXT_FIELDS:
        related = context if context is not None else _normalized_context(row)
        value = related.get(name)
    else:
        value = row.value(name)
    if _blank(value):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name}: 문자열이 필요합니다.")
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
    """수정하지 않은 AI 값이 가리키는 원본 셀과 맞는지 확인한다.

    param
    - draft, row, name: 원본 초안·검토 행·검사 항목. BE-007에서 대조한 정규값은 재대조하지 않음.

    return
    - 원본 위치·값을 확인해야 하는 문제. 문제가 없으면 None.
    """
    value = row.original.get(name)
    if name in row.edits or name in row.normalized or _blank(value) or not isinstance(value, str):
        return None
    address = row.sources.get(name)
    label = FIELD_LABELS[name]
    if not address:
        return Issue(IssueLevel.ERROR, f"{label}: 원본 셀 위치가 없습니다. 원본에서 값을 확인하세요.",
                     row.row_id, name, code="SOURCE_REQUIRED")
    parts = address.split(":")
    if len(parts) > 2 or not all(in_sheet(draft.sheet, part) for part in parts):
        return Issue(IssueLevel.ERROR, f"{label}: 원본 위치 {address}가 읽은 시트 범위 밖이거나 주소 형식이 아닙니다.",
                     row.row_id, name, address, code="SOURCE_OUTSIDE")
    if len(parts) == 2:
        return Issue(IssueLevel.ERROR, f"{label}: 범위 주소라 원본 값 일치를 자동으로 확인하지 못했습니다.",
                     row.row_id, name, address, code="SOURCE_RANGE")
    original = cell_text(value_at(draft.sheet, address)).strip()
    if not _same_text(original, value.strip()):
        return Issue(IssueLevel.ERROR, f"{label}: AI 값이 원본 셀 값과 다릅니다 · 원본 {address}: {original or '빈 칸'}",
                     row.row_id, name, address, code="SOURCE_MISMATCH")
    return None


def _has_source_position(draft: ImportDraft, row: ReviewRow, name: str) -> bool:
    """원본 식별값의 위치가 실제 읽은 단일 셀로 확인됐는지 검사한다."""
    address = row.sources.get(name)
    if not isinstance(address, str) or not in_sheet(draft.sheet, address):
        return False
    top, left = parse_address(address)
    return cell_address(top, left) in draft.sheet.cells


def _is_hpa_row(draft: ImportDraft, row: ReviewRow, locations=()) -> bool:
    """현재 표기를 지워도 해당 행의 실제 원본 HPA 근거와 필수 오류를 유지한다."""
    classifiers = ("model_name", "group_name", "family_name")
    if any(isinstance(values.get(name), str) and values[name].strip().casefold() == "hpa"
           for values in (row.original, row.normalized, {name: row.value(name) for name in classifiers})
           for name in classifiers):
        return True
    sources = {name: cell_address(*parse_address(address)) for name, address in row.sources.items()
               if isinstance(address, str) and _has_source_position(draft, row, name)}
    # AI가 분류 항목을 전부 빠뜨려도 호기·SW·PM의 실제 위치가 속한 HPA 레코드는 남는다.
    identity_fields = ("equipment_code", "version_raw", "pm_name")
    matched = [location for location in locations
               if any(name in location.fields and sources.get(name) == location.fields[name].address for name in identity_fields)]
    if matched:
        return any(location.is_hpa for location in matched)
    # 머리글 없는 수동 검토도 같은 원본 행의 HPA 분류 출처는 보존한다.
    # 별도의 HPA 제목만으로 다른 행의 일반 설비까지 HPA로 간주하지 않는다.
    identity_rows = {parse_address(sources[name])[0] for name in identity_fields if name in sources}
    return any(name in sources and isinstance(value_at(draft.sheet, sources[name]), str)
               and value_at(draft.sheet, sources[name]).strip().casefold() == "hpa"
               and parse_address(sources[name])[0] in identity_rows for name in classifiers)


def _row_issues(draft: ImportDraft, row: ReviewRow) -> list[Issue]:
    """필수값과 저장 형식을 검사하며 핫픽스 접미사의 모양은 제한하지 않는다.

    param
    - draft: 원본 위치를 포함한 가져오기 초안.
    - row: 사용자가 수정한 현재 검토 행.

    return
    - 필수값·자료형·길이·날짜와 원본 대조 결과 목록.
    """
    issues = []
    for name in draft.fields:
        value, address, label = row.value(name), row.sources.get(name), FIELD_LABELS[name]
        if name in SOURCE_REQUIRED_FIELDS and (name in REQUIRED[draft.kind] or not _blank(value)) \
                and not _has_source_position(draft, row, name):
            issues.append(Issue(IssueLevel.ERROR, f"{label}: 원본 셀 위치를 확인해야 합니다. 값을 수정해도 위치 확인은 필요합니다.",
                                row.row_id, name, address, code="REQUIRED_SOURCE_UNRESOLVED"))
        if value is not None and not isinstance(value, str):
            issues.append(Issue(IssueLevel.ERROR, f"{label}: 문자열 또는 빈 값이어야 합니다.",
                                row.row_id, name, address, code="INVALID_TYPE"))
            continue
        if name in REQUIRED[draft.kind] and _blank(value):
            issues.append(Issue(IssueLevel.ERROR, f"{label}: 필수값이 비어 있습니다.", row.row_id, name, address))
            continue
        if _blank(value):
            continue
        if name in MAX_LENGTH and len(value) > MAX_LENGTH[name]:
            issues.append(Issue(IssueLevel.ERROR, f"{label}: {MAX_LENGTH[name]}자 이내여야 합니다.", row.row_id, name, address))
        if name in ("equipment_code", "version_raw") and ("\n" in value or "\r" in value):
            issues.append(Issue(IssueLevel.ERROR, f"{label}: 원문에 줄바꿈이 있습니다. 형식 오류입니다.", row.row_id, name, address))
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            issues.append(Issue(IssueLevel.ERROR, f"{label}: UTF-8로 저장할 수 없는 문자입니다.",
                                row.row_id, name, address, code="INVALID_ENCODING"))
        else:
            if name == "content" and len(encoded) > 65535:
                issues.append(Issue(IssueLevel.ERROR, "변경 내용: 저장 가능한 크기(65535바이트)를 넘었습니다.",
                                    row.row_id, name, address, code="TEXT_TOO_LONG"))
        if value.startswith(("#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!")):
            issues.append(Issue(IssueLevel.ERROR, f"{label}: Excel 오류값을 보완해야 합니다.",
                                row.row_id, name, address, code="EXCEL_ERROR"))
        if name in DATE_FIELDS:
            try:
                parse_date(value)
            except ValueError:
                issues.append(Issue(IssueLevel.ERROR, f"{label}: 날짜 형식(YYYY-MM-DD)이 아닙니다.", row.row_id, name, address))
        if name == "item_no":
            number = value.strip()
            if not (number.isascii() and number.isdecimal()) or len(number) > 10 or int(number) > 2147483647:
                issues.append(Issue(IssueLevel.ERROR, f"{label}: 0~2147483647 범위의 숫자여야 합니다.", row.row_id, name, address))
        if name == "production_area" and value.strip().upper() not in PRODUCTION_AREAS:
            issues.append(Issue(IssueLevel.ERROR, f"{label}: Memory 또는 Foundry이거나 비워 두어야 합니다.",
                                row.row_id, name, address))
        if (source := _source_issue(draft, row, name)) is not None:
            issues.append(source)
    values = {name: row.value(name) for name in draft.fields}
    if all(value is None or isinstance(value, str) for value in values.values()):
        _, _, notes = normalize(values, draft.kind, row.row_id, {})
        checked_fields = {"group_name", "ui_type", "structure_type", "modification_mark"}
        if (row.value("model_name") or "") in {"C2", "M-C2"}:
            checked_fields.add("model_name")
        for note in notes:
            if note.field in checked_fields:
                issues.append(Issue(IssueLevel.ERROR, note.reason, row.row_id, note.field,
                                    row.sources.get(note.field), code="REFERENCE_FIELD_CONFLICT"))
    if draft.kind is ExtractKind.RELEASE_NOTE and not _blank(row.value("item_no")) and _blank(row.value("content")):
        issues.append(Issue(IssueLevel.ERROR, "변경 내용: Item 번호가 있으면 변경 내용이 필요합니다.",
                            row.row_id, "content", row.sources.get("content")))
    return issues


def _safe(row: ReviewRow, name: str) -> Any:
    """형식 오류가 있는 값을 그룹 비교에 사용하지 않는다.

    param
    - row, name: 현재 검토 행과 항목 이름.

    return
    - 비교할 정규 값. 형식 오류이면 None.
    """
    try:
        return _normalized(row, name)
    except (ValueError, TypeError, AttributeError):
        return None


def _group(rows: Iterable[ReviewRow], names: tuple[str, ...]) -> dict[tuple, list[ReviewRow]]:
    groups: dict[tuple, list[ReviewRow]] = defaultdict(list)
    for row in rows:
        key = tuple(_safe(row, name) for name in names)
        if None not in key:
            groups[key].append(row)
    return groups


def _workbook_site_issues(rows: tuple[ReviewRow, ...]) -> list[Issue]:
    """사업장 단위로 관리하는 현황 통합문서에 다른 사업장이 섞였는지 검사한다."""
    if len({_safe(row, "site") for row in rows} - {None}) <= 1:
        return []
    return [Issue(IssueLevel.ERROR, "설비 현황 통합문서에 서로 다른 사업장이 섞여 있습니다. 사업장별 파일과 내용을 확인하세요.",
                  row.row_id, "site", row.sources.get("site"), code="SITE_CONFLICT") for row in rows]


def _equipment_issues(rows: tuple[ReviewRow, ...]) -> list[Issue]:
    """같은 호기·날짜의 기준정보와 고정 PM 위치의 충돌을 검사한다.

    param
    - rows: 제외되지 않은 전체 설비 검토 행.

    return
    - 차단 충돌, 정확한 중복, 비차단 PM 버전 불일치 목록.
    """
    issues = []
    # 사업장은 날짜별 변경값이 아니라 호기의 고정 소속이다.
    for (code,), members in _group(rows, ("equipment_code",)).items():
        sites = {_safe(row, "site") for row in members} - {None}
        if len(sites) > 1:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR,
                                    f"같은 설비({code})의 사업장이 서로 다릅니다. 고정 소속 사업장을 확인하세요.",
                                    row.row_id, "site", row.sources.get("site"), code="SITE_CONFLICT"))
    snapshot_fields = ("line", "group_name", "production_area",
                       "structure_type", "geometry_type", "modification_type")
    for (_, code), members in _group(rows, ("base_date", "equipment_code")).items():
        differing = [FIELD_LABELS[n] for n in snapshot_fields if len({_safe(r, n) for r in members} - {None}) > 1]
        if differing:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, f"같은 설비({code})·기준일인데 {', '.join(differing)} 값이 행마다 다릅니다.",
                                    row.row_id, "equipment_code", row.sources.get("equipment_code")))
        with_pm = [r for r in members if not _blank(r.value("pm_name"))]
        if with_pm and len(with_pm) != len(members):
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, "PM 구분이 있는 현황에서는 모든 SW의 PM 위치를 확인해야 합니다.",
                                    row.row_id, "pm_name", row.sources.get("pm_name"), code="PM_LINK_MISSING"))
        if not with_pm and len({_safe(r, "model_name") for r in members}) > 1:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, "PM 구분 없이 같은 설비의 모델이 서로 다릅니다.",
                                    row.row_id, "model_name", row.sources.get("model_name"), code="MODEL_CONFLICT"))
    # 고정 PM 위치는 계열과 무관하게 한 모델·한 SW에만 연결할 수 있다.
    for (_, _, pm), members in _group(rows, ("base_date", "equipment_code", "pm_name")).items():
        assignments = {(_row_family(r), _safe(r, "model_name"), _safe(r, "version_raw")) for r in members
                       if _safe(r, "version_raw") is not None}
        if len(assignments) > 1:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, f"같은 PM({pm}) 위치의 모델·계열·SW 값이 상충합니다.",
                                    row.row_id, "version_raw", row.sources.get("version_raw"), code="PM_CONFLICT"))
    by_pm: dict[tuple, list[ReviewRow]] = defaultdict(list)
    for row in rows:
        key = (_safe(row, "base_date"), _safe(row, "equipment_code"), _row_family(row))
        if None not in key:
            by_pm[(*key, _safe(row, "pm_name") or "")].append(row)
    by_family: dict[tuple, set] = defaultdict(set)
    family_members = defaultdict(list)
    for (*family_key, pm), members in by_pm.items():
        versions = {_safe(r, "version_raw") for r in members} - {None}
        by_family[tuple(family_key)] |= versions
        family_members[tuple(family_key)].append(members)
        pm_label = pm or "PM 구분 없음"
        if len(versions) > 1 and not pm:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, f"같은 PM({pm_label})·SW 계열에 서로 다른 버전이 있습니다. 하나만 남기세요.",
                                    row.row_id, "version_raw", row.sources.get("version_raw")))
        seen = {}
        for row in members:
            exact = tuple((name, _safe(row, name)) for name in FIELDS[ExtractKind.EQUIPMENT_STATUS])
            if exact in seen:
                issues.append(Issue(IssueLevel.REVIEW, "다른 추출 행과 정확히 같은 행입니다. 한 행으로 합쳐 저장합니다.",
                                    row.row_id, "version_raw", row.sources.get("version_raw"), code="DUPLICATE"))
            seen.setdefault(exact, row.row_id)
    for family_key, versions in by_family.items():
        _, code, family = family_key
        pms = family_members[family_key]
        if (len(versions) > 1 and len(pms) > 1 and all(not _blank(r.value("pm_name")) for m in pms for r in m)
                and all(len({_safe(r, "version_raw") for r in m} - {None}) == 1 for m in pms)):
            for members in pms:
                issues.append(Issue(IssueLevel.INFO, f"{code}의 PM별 {family[1]} 버전이 다릅니다. PM 불일치 예외로 표시되며 저장할 수 있습니다.",
                                    members[0].row_id, "pm_name", members[0].sources.get("pm_name"), code="PM_VERSION_MISMATCH"))
    return issues


def _row_family(row: ReviewRow, kind=ExtractKind.EQUIPMENT_STATUS) -> tuple:
    """같은 이름의 계열도 UI·분류·구조에 따라 구분하는 비교 키를 반환한다.

    param
    - row: 현재 값을 가진 검토 행.
    - kind: 설비 현황 또는 릴리즈 노트.

    return
    - 확정된 계열 구분. 불명확하면 입력 구분값 그대로 구성한 보수적인 키.
    """
    key = family_key(row, kind)
    return identity(key) if key else tuple(_safe(row, name) for name in (
        "group_name", "family_name", "ui_type", "structure_type",
    ))


def _release_issues(rows: tuple[ReviewRow, ...]) -> list[Issue]:
    """같은 계열·릴리즈·원본 Item 번호의 중복과 상충 값을 검사한다.

    param
    - rows: 제외되지 않은 릴리즈 검토 행.

    return
    - 계열별 출시일·Item 충돌과 중복 확인 목록.
    """
    issues = []
    groups = defaultdict(list)
    items = defaultdict(list)
    for row in rows:
        version, number = _safe(row, "version_raw"), _safe(row, "item_no")
        if version is not None:
            key = (_row_family(row, ExtractKind.RELEASE_NOTE), version)
            groups[key].append(row)
            if number is not None:
                items[(*key, number)].append(row)
    for (_, version), members in groups.items():
        dates = {_safe(r, "release_date") for r in members} - {None}
        if len(dates) > 1:
            for row in members:
                issues.append(Issue(IssueLevel.ERROR, f"같은 버전({version})의 출시일이 행마다 다릅니다.",
                                    row.row_id, "release_date", row.sources.get("release_date")))
    for (_, version, number), members in items.items():
        contents = {_safe(r, "content") for r in members}
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
    """현재 관리값·릴리즈 등록·확인된 순서를 검토값과 비교한다.

    param
    - draft: 수정 후의 가져오기 초안.
    - reference: 같은 조회에서 확보한 저장 자료. None이면 미연결로 안내.

    return
    - 신규·변경·미등록 안내와 N 비교 결과. 미등록·순서 미확인만으로 차단하지 않음.
    """
    issues = []
    if reference is None:
        issues.append(Issue(IssueLevel.INFO, "기준 자료 미연결 · 기존값과 릴리즈 등록 여부를 확인하지 않았으며 버전 비교는 판단 불가입니다.",
                            code="REFERENCE_NOT_CONNECTED"))
        return issues
    for row in draft.active_rows:
        code, family, version = (_safe(row, n) for n in ("equipment_code", "family_name", "version_raw"))
        if draft.kind is ExtractKind.EQUIPMENT_STATUS and code is not None:
            sites = set(reference.equipment_sites.get(code, ()))
            # 순수 계산 호출자가 정확한 날짜 현황만 주입한 기존 계약도 지원한다.
            sites.update(snapshot.site for snapshot in reference.snapshots if snapshot.equipment_code == code)
            site = _safe(row, "site")
            if len(sites) > 1 or sites and site is not None and site not in sites:
                previous = next(iter(sites)) if len(sites) == 1 else tuple(sorted(sites))
                reason = (f"설비({code})의 기존 기록에 서로 다른 사업장이 있습니다. 고정 소속 사업장을 확인해야 합니다."
                          if len(sites) > 1 else
                          f"설비({code})의 고정 소속 사업장은 {previous!r}입니다. 다른 사업장으로 변경할 수 없습니다.")
                issues.append(Issue(IssueLevel.ERROR, reason, row.row_id, "site", row.sources.get("site"),
                                    code="SITE_CONFLICT", previous_value=previous, current_value=site))
        if draft.kind is ExtractKind.EQUIPMENT_STATUS and code is not None and code not in reference.equipment_codes:
            issues.append(Issue(IssueLevel.REVIEW, f"등록되지 않은 설비 호기({code})입니다. 저장하면 새 설비로 등록됩니다.",
                                row.row_id, "equipment_code", row.sources.get("equipment_code"), code="NEW_EQUIPMENT"))
        if (problem := model_issue(row, draft.kind, reference)) is not None:
            issues.append(problem)
            continue
        resolved = resolve_family(row, draft.kind, reference)
        if resolved is not None:
            if version is None:
                continue
            is_hpa = resolved.key.category == "HPA"
            registered = resolved.releases.match(version)
            if registered is None and draft.kind is ExtractKind.EQUIPMENT_STATUS:
                message = ("릴리즈 미등록 HPA 버전입니다. 원문을 보존하며 저장할 수 있고 공통 분석에서는 제외합니다."
                           if is_hpa else "릴리즈 미등록 버전입니다. 원문을 보존하며 저장할 수 있지만 비교는 판단 불가입니다.")
                issues.append(Issue(IssueLevel.REVIEW, message,
                                    row.row_id, "version_raw", row.sources.get("version_raw"), code="RELEASE_UNREGISTERED"))
            if draft.kind is ExtractKind.EQUIPMENT_STATUS:
                if is_hpa:
                    issues.append(Issue(IssueLevel.INFO, "HPA는 저장·조회하며 전체 설비 수·N 이상 적용률·공통 분석에서 제외합니다.",
                                        row.row_id, "version_raw", row.sources.get("version_raw"), code="HPA_ANALYSIS_EXCLUDED"))
                else:
                    model = _safe(row, "model_name")
                    model = "M-O5" if model == "M5" else model
                    comparison = compare_version(resolved.releases, version, resolved.targets.get(model))
                    issues.append(Issue(IssueLevel.INFO,
                        f"N 비교: {comparison.label}" + (f" · {comparison.message}" if comparison.message else ""),
                        row.row_id, "version_raw", row.sources.get("version_raw"), code="VERSION_COMPARISON", comparison=comparison))
                issues.extend(_stored_equipment_issues(draft, row, resolved.releases.family_id, reference))
            else:
                issues.extend(_stored_release_issues(row, resolved.releases.family_id, reference))
            continue
        if reference.catalog_loaded or reference.families:
            issues.append(Issue(IssueLevel.ERROR, "모델·분류·UI·구조와 일치하는 SW 계열이 없거나 여러 개입니다. 계열 대응을 확인하세요.",
                                row.row_id, "family_name", row.sources.get("family_name"), code="FAMILY_UNRESOLVED"))
        elif family is None or version is None:
            continue
        elif family not in reference.release_versions:
            issues.append(Issue(IssueLevel.REVIEW, f"등록되지 않은 SW 계열({family})입니다.",
                                row.row_id, "family_name", row.sources.get("family_name")))
        elif version not in reference.release_versions[family]:
            issues.append(Issue(IssueLevel.REVIEW, "등록되지 않은 버전입니다. 원문을 보존하고 릴리즈 연결 없이 저장합니다.",
                                row.row_id, "version_raw", row.sources.get("version_raw"), code="RELEASE_UNREGISTERED"))
        if not reference.catalog_loaded and not reference.families:
            issues.append(Issue(IssueLevel.INFO, "계열 ID와 확인된 릴리즈 순서가 미연결되어 버전 비교는 판단 불가입니다.",
                                row.row_id, "version_raw", row.sources.get("version_raw"), code="ORDER_NOT_CONNECTED"))
    return issues


def _change(row, field, previous, current) -> Issue:
    """원본을 바꾸지 않고 기존값·새 값의 확인 항목을 만든다.

    param
    - row, field: 변경이 발견된 검토 행과 항목 이름.
    - previous, current: 기존 관리값과 이번 검토값. None도 값으로 구분.

    return
    - 사용자 확인용 비차단 변경 안내.
    """
    return Issue(IssueLevel.REVIEW, f"{FIELD_LABELS[field]} 변경 확인: 기존 {previous!r} → 새 값 {current!r}",
                 row.row_id, field, row.sources.get(field), code="STORED_VALUE_CHANGED",
                 previous_value=previous, current_value=current)


def _stored_equipment_issues(draft, row, family_id, reference) -> list[Issue]:
    """같은 호기·정확한 날짜·고정 PM 위치의 저장값을 비교한다.

    param
    - draft, row: 설비 초안과 현재 행.
    - family_id: 확인된 계열 ID.
    - reference: 날짜를 임의로 채우지 않은 기존 저장 자료.

    return
    - 동일값 유지 안내 또는 기존값·새 값·해당 PM의 변경 확인 목록.
    """
    matches = [snapshot for snapshot in reference.snapshots
               if snapshot.equipment_code == _safe(row, "equipment_code")
               and snapshot.base_date.isoformat() == _safe(row, "base_date")]
    if not matches:
        return []
    old = matches[0]
    issues = []
    pm = _safe(row, "pm_name")
    if pm is not None and any(sw.family_id == family_id and sw.version_raw != _safe(row, "version_raw")
                              and any(p.pm_name != pm for p in sw.pms) for sw in old.software):
        issues.append(Issue(IssueLevel.INFO, "기존 다른 PM의 같은 계열 버전과 차이가 있습니다. PM별 원문을 보존하며 이 차이만으로 저장을 막지 않습니다.",
                            row.row_id, "pm_name", row.sources.get("pm_name"), code="STORED_PM_VERSION_DIFFERENCE"))
    for name in ("line", "production_area", "structure_type", "geometry_type", "modification_type"):
        if getattr(old, name) != _safe(row, name):
            issues.append(_change(row, name, getattr(old, name), _safe(row, name)))
    software = [sw for sw in old.software if (
        any(p.pm_name == pm for p in sw.pms) if pm else not sw.pms and sw.family_id == family_id
    )]
    if not software:
        issues.append(Issue(IssueLevel.REVIEW, "기존 날짜 현황에 없는 PM·SW 연결입니다. 추가할 위치와 계열을 확인하세요.",
                            row.row_id, "pm_name", row.sources.get("pm_name"), code="NEW_SOFTWARE_LINK"))
        return issues
    if len(software) != 1:
        return [Issue(IssueLevel.ERROR, "기존 저장 자료의 같은 PM·계열 연결이 여러 개입니다. 기존 자료를 확인하세요.",
                      row.row_id, "pm_name", row.sources.get("pm_name"), code="STORED_LINK_CONFLICT")]
    previous = software[0]
    if previous.family_id != family_id:
        issues.append(_change(row, "family_name", previous.family_id, family_id))
    if previous.version_raw != _safe(row, "version_raw"):
        issues.append(_change(row, "version_raw", previous.version_raw, _safe(row, "version_raw")))
    old_model = next((p.model_name_raw for p in previous.pms if p.pm_name == pm), old.model_name)
    if old_model != _safe(row, "model_name"):
        issues.append(_change(row, "model_name", old_model, _safe(row, "model_name")))
    if not any(issue.code in {"STORED_VALUE_CHANGED", "NEW_SOFTWARE_LINK"} for issue in issues):
        issues.append(Issue(IssueLevel.INFO, "기존 같은 호기·날짜·PM·계열의 값과 같습니다. 기존값 유지 대상입니다.",
                            row.row_id, "version_raw", row.sources.get("version_raw"), code="STORED_VALUE_IDENTICAL"))
    return issues


def _stored_release_issues(row, family_id, reference) -> list[Issue]:
    """같은 계열·버전·원본 Item 번호의 기존 관리값을 비교한다.

    param
    - row: 수정 후 릴리즈 검토 행.
    - family_id: 확인된 계열 ID.
    - reference: 기존 릴리즈와 Item의 관리값.

    return
    - 변경 확인 목록. 같은 번호가 여러 개이면 자동 대응하지 않고 차단.
    """
    matches = [old for old in reference.stored_releases if old.family_id == family_id
               and old.version_raw == _safe(row, "version_raw")
               and (str(old.values["item_no"]) if old.values.get("item_no") is not None else None)
               == (str(_safe(row, "item_no")) if _safe(row, "item_no") is not None else None)]
    if len(matches) > 1:
        return [Issue(IssueLevel.ERROR, "기존 릴리즈의 같은 Item 번호가 여러 개입니다. 대응 대상을 확인하세요.",
                      row.row_id, "item_no", row.sources.get("item_no"), code="STORED_ITEM_AMBIGUOUS")]
    if not matches:
        return []
    old = matches[0]
    return [_change(row, name, old.values[name], _safe(row, name))
            for name in ("release_date", "content") if name in old.values and old.values[name] != _safe(row, name)]


def _hpa_history_issues(drafts, reference, target_draft):
    """이번 묶음의 날짜별 분류와 유지할 기존 이력이 상충하면 원본 확인을 요청한다."""
    incoming, candidates = defaultdict(lambda: defaultdict(set)), []
    for item in drafts:
        if item.kind is not ExtractKind.EQUIPMENT_STATUS:
            continue
        for row in item.active_rows:
            code, day = _safe(row, "equipment_code"), _safe(row, "base_date")
            excluded = analysis_excluded(row, item.kind, reference)
            if code is None or day is None or excluded is None:
                continue
            incoming[code][day].add(excluded)
            candidates.append((item, row, code))
    conflicts = {}
    for code, dates in incoming.items():
        previous = reference.equipment_classifications.get(code, {}) if reference is not None else {}
        entries = sorted((day, hpa) for day, kinds in dates.items() for hpa in kinds)
        all_entries = entries + [(day, hpa) for day, kinds in previous.items() for hpa in kinds]
        by_kind = {kind: sorted({day for day, hpa in all_entries if hpa is kind}) for kind in (False, True)}
        pair = None
        for day, hpa in entries:
            other_day = next((other for other in by_kind[not hpa] if other != day), None)
            if other_day is not None:
                pair = (other_day, not hpa), (day, hpa)
                break
        if pair is not None:
            conflicts[code] = " / ".join(f"{day} {'HPA' if hpa else '일반 설비'}" for day, hpa in pair)
    return [Issue(IssueLevel.ERROR,
        f"설비({code})의 날짜별 HPA·일반 분류가 다릅니다 ({conflicts[code]}). "
        "원본 호기와 모델·설비군을 확인·보완한 뒤 다시 검증하세요. 이번 저장 묶음 전체를 보류합니다.",
        row.row_id, "model_name", row.sources.get("model_name"), code="HPA_HISTORY_CONFLICT")
        for item, row, code in candidates if item is target_draft and code in conflicts]


def validate(draft: ImportDraft, reference: ReferenceData | None = None,
             *, batch_drafts: tuple[ImportDraft, ...] | None = None) -> tuple[Issue, ...]:
    """현재 초안 전체를 재검증하고 파일·시트·원본 위치를 포함한 결과를 반환한다.

    param
    - draft: 원본과 사용자 수정값을 보존한 가져오기 초안.
    - reference: 기존 현황·릴리즈와 확인된 순서. 미연결이면 None.
    - batch_drafts: 같은 묶음의 날짜별 분류를 함께 검사할 전체 초안. 단일 검증이면 생략.

    return
    - 오류·확인·안내 순으로 정렬한 결과. ERROR가 하나라도 있으면 전체 묶음을 보류.
    """
    excluded_hpa = set()
    if draft.kind is ExtractKind.EQUIPMENT_STATUS and any(row.excluded for row in draft.rows):
        from .source_sheet import sheet_from_snapshot

        locations = record_locations(sheet_from_snapshot(draft.workbook_name, draft.sheet))
        excluded_hpa = {row.row_id for row in draft.rows if row.excluded and _is_hpa_row(draft, row, locations)}
    issues = [Issue(IssueLevel.REVIEW, note.reason, None, note.field, note.address) for note in draft.notes]
    for issue in draft.extraction_issues:
        if issue.row_id is None:
            issues.append(issue)
        else:
            row = draft.row(issue.row_id)
            if row.excluded and not (row.row_id in excluded_hpa and issue.field in REQUIRED[draft.kind] and issue.blocks_save):
                continue
            if issue.code == "REQUIRED_SOURCE_UNRESOLVED":
                if issue.field is None or not _has_source_position(draft, row, issue.field):
                    issues.append(issue)
                continue
            edited = row.edits
            # 명시적으로 수정한 항목은 현재값으로 재검증하며 원래 추출 사유로 계속 막지 않는다.
            family_fields = {"group_name", "model_name", "family_name", "ui_type", "structure_type"}
            if issue.field not in edited and not (issue.field == "family_name" and family_fields.intersection(edited)):
                issues.append(issue)
    rows = draft.active_rows
    if not rows:
        issues.append(Issue(IssueLevel.ERROR, "저장할 행이 없습니다. 제외한 행을 다시 포함하거나 다시 추출하세요."))
    for row in rows:
        issues.extend(_row_issues(draft, row))
    if draft.kind is ExtractKind.EQUIPMENT_STATUS:
        for row in draft.rows:
            if row.row_id in excluded_hpa:
                issues.extend(issue for issue in _row_issues(draft, row)
                              if issue.blocks_save and issue.field in REQUIRED[draft.kind])
    if draft.kind is ExtractKind.EQUIPMENT_STATUS:
        issues.extend(_workbook_site_issues(rows))
        issues.extend(_equipment_issues(rows))
    else:
        issues.extend(_release_issues(rows))
    issues.extend(_reference_issues(draft, reference))
    issues.extend(_hpa_history_issues(batch_drafts if batch_drafts is not None else (draft,), reference, draft))
    order = {IssueLevel.ERROR: 0, IssueLevel.REVIEW: 1, IssueLevel.INFO: 2}
    return tuple(replace(issue, workbook_name=draft.workbook_name, sheet_name=draft.sheet.sheet_name,
                         workbook_id=draft.workbook_id)
                 for issue in sorted(issues, key=lambda i: (order[i.level], i.row_id is None, i.row_id or 0)))


def validate_batch(drafts: tuple[ImportDraft, ...], reference: ReferenceData | None = None) -> tuple[Issue, ...]:
    """여러 파일·시트의 검토값을 한 묶음으로 검증한다.

    param
    - drafts: 함께 저장할 전체 초안. 파일·시트는 서로 달라도 됨.
    - reference: 묶음 전체에 대해 함께 읽은 기존 저장 자료.

    return
    - 각 시트 내부와 시트 사이의 문제 목록. 하나의 ERROR라도 전체 저장을 보류.
    """
    if not drafts:
        return (Issue(IssueLevel.ERROR, "검증할 저장 묶음이 없습니다.", code="EMPTY_BATCH"),)
    if len(drafts) == 1:
        return validate(drafts[0], reference)
    issues = [issue for draft in drafts for issue in validate(draft, reference, batch_drafts=drafts)]
    lookup = {}
    rows_by_kind = defaultdict(list)
    equipment_by_workbook = defaultdict(list)
    for draft in drafts:
        for row in draft.active_rows:
            index = len(lookup) + 1
            lookup[index] = (draft, row)
            rows_by_kind[draft.kind].append(replace(row, row_id=index))
            if draft.kind is ExtractKind.EQUIPMENT_STATUS:
                workbook = ("id", draft.workbook_id) if draft.workbook_id is not None else ("name", draft.workbook_name)
                equipment_by_workbook[workbook].append(replace(row, row_id=index))
    for rows in equipment_by_workbook.values():
        for issue in _workbook_site_issues(tuple(rows)):
            draft, row = lookup[issue.row_id]
            issues.append(replace(issue, row_id=row.row_id, workbook_name=draft.workbook_name,
                                  sheet_name=draft.sheet.sheet_name, workbook_id=draft.workbook_id))
    for kind, rows in rows_by_kind.items():
        for issue in (_equipment_issues(tuple(rows)) if kind is ExtractKind.EQUIPMENT_STATUS else _release_issues(tuple(rows))):
            draft, row = lookup[issue.row_id]
            issues.append(replace(issue, row_id=row.row_id, workbook_name=draft.workbook_name,
                                  sheet_name=draft.sheet.sheet_name, workbook_id=draft.workbook_id))
    unique = {}
    for issue in issues:
        key = (issue.workbook_id, issue.workbook_name, issue.sheet_name, issue.row_id, issue.field, issue.address, issue.message)
        unique.setdefault(key, issue)
    return tuple(sorted(unique.values(), key=lambda issue: (
        {IssueLevel.ERROR: 0, IssueLevel.REVIEW: 1, IssueLevel.INFO: 2}[issue.level],
        issue.workbook_name or "", issue.sheet_name or "", issue.row_id or 0,
    )))


def has_errors(issues: Iterable[Issue]) -> bool:
    """전체 결과에 저장을 차단하는 오류가 있는지 확인한다.

    param
    - issues: 한 시트 또는 전체 묶음의 검증 결과.

    return
    - ERROR가 하나라도 있으면 True. 비차단 PM 차이·미등록·순서 미확인만 있으면 False.
    """
    return any(issue.level is IssueLevel.ERROR for issue in issues)


# --- 저장 요청 -------------------------------------------------------------

def build_save_request(draft: ImportDraft, request_id: str, reference: ReferenceData | None = None,
                       *, batch_drafts: tuple[ImportDraft, ...] | None = None) -> SaveRequest:
    """초안 전체를 재검증하고 정확한 중복의 출처를 합쳐 저장 요청으로 만든다.

    param
    - draft: 원본과 현재 수정값을 가진 한 시트 초안.
    - request_id: 중복 저장 확인에 사용할 요청 식별자.
    - reference: 기존값과 릴리즈 비교 자료. DB 조회는 ImportValidationService에서 주입.

    return
    - 원본 셀과 필드별 출처 목록을 따로 보존한 저장 요청. DB에는 쓰지 않음.

    raises
    - ImportBlockedError: 검증 오류가 하나라도 남아 있는 경우.
    """
    issues = validate(draft, reference, batch_drafts=batch_drafts)
    if has_errors(issues):
        raise ImportBlockedError("오류가 남아 있어 저장할 수 없습니다.")
    merged: dict[tuple, SaveRow] = {}
    for row in draft.active_rows:
        context = _normalized_context(row)
        values = {name: _normalized(row, name, context=context) for name in draft.fields}
        family = resolve_family(row, draft.kind, reference) if reference is not None else None
        release = family.releases.match(values.get("version_raw")) if family is not None else None
        sources = {
            name: {"sheet": draft.sheet.sheet_name, "cells": [address]}
            for name, address in row.sources.items() if name in draft.fields
        }
        key = tuple(values[name] for name in draft.fields)
        if key in merged:
            previous = merged[key]
            combined = merge_sources(previous.source_reference, sources)
            merged[key] = replace(previous, row_ids=(*previous.row_ids, row.row_id), source_reference=combined or None,
                                  edited_fields=tuple(sorted({*previous.edited_fields, *row.edited_fields})))
        else:
            merged[key] = SaveRow((row.row_id,), values, sources or None, row.edited_fields,
                                 family_id=family.releases.family_id if family is not None else None,
                                 release_version_id=release.release_version_id if release is not None else None)
    raw_data = {"sheet": draft.sheet.sheet_name, "cells": deepcopy(draft.sheet.cells), "merged": list(draft.sheet.merged)}
    return SaveRequest(
        request_id=request_id,
        kind=draft.kind,
        workbook_name=draft.workbook_name,
        raw_data=raw_data,
        rows=tuple(merged.values()),
        review_count=sum(issue.level is IssueLevel.REVIEW for issue in issues),
        ai_source=draft.ai_source,
        workbook_id=draft.workbook_id,
        expected_snapshots=deepcopy(tuple(snapshot for snapshot in reference.snapshots
            if (snapshot.equipment_code, snapshot.base_date.isoformat()) in {
                (row.values.get("equipment_code"), row.values.get("base_date")) for row in merged.values()
            })) if reference is not None and draft.kind is ExtractKind.EQUIPMENT_STATUS else None,
    )


class ImportValidationService:
    """기존 DB 자료와 대조하는 가져오기 검증 서비스.

    실제 저장·재가져오기 갱신은 BE-009에서 수행한다. 검증과 요청 준비는 DB를 변경하지 않는다.
    """

    def __init__(self, storage):
        """공통 저장 서비스와 읽기 레포지토리를 연결한다.

        param
        - storage: 기존 관리 자료를 조회할 StorageService.
        """
        from .repository import ImportValidationRepository

        self.storage = storage
        self.repository = ImportValidationRepository()

    def validate(self, drafts, *, targets=None, aliases=None):
        """현재 DB 자료를 읽고 전체 검토 묶음의 문제와 저장 가능 여부를 반환한다.

        param
        - drafts: 함께 검증할 ImportDraft 튜플.
        - targets, aliases: 모델·계열별 목표 N과 계열별 확정 표기 대응. 미지정이면 추정하지 않음.

        return
        - 파일·시트·행·열·사유·비교 결과를 포함한 Issue 튜플. blocks_save로 저장 차단 확인.

        raises
        - DatabaseError: DB 조회에 실패한 경우. 성공한 검증으로 대신하지 않음.
        - StorageValidationError: 기존 저장 자료를 읽을 수 없는 경우.
        """
        with self.storage.transaction() as tx:
            reference = self.repository.load(tx, drafts, targets=targets, aliases=aliases)
            return validate_batch(drafts, reference)

    def prepare(self, drafts, request_id, *, targets=None, aliases=None):
        """모든 시트의 재검증을 통과한 경우에만 저장 요청 묶음을 만든다.

        param
        - drafts: 함께 저장할 전체 초안 튜플.
        - request_id: 요청 묶음의 식별자. 시트별 요청에는 순번을 추가.
        - targets, aliases: 모델·계열별 목표 N과 확인된 버전 별칭.

        return
        - 전체 검증을 통과한 SaveRequest 튜플. 실제 DB 반영은 수행하지 않음.

        raises
        - ImportBlockedError: 오류가 하나라도 남은 경우. 정상 시트만 먼저 반환하지 않음.
        - DatabaseError: 최신 기존값 조회에 실패한 경우.
        """
        with self.storage.transaction() as tx:
            reference = self.repository.load(tx, drafts, targets=targets, aliases=aliases)
            errors = [issue for issue in validate_batch(drafts, reference) if issue.blocks_save]
            if errors:
                raise ImportBlockedError("오류가 남아 있어 이번 묶음 전체의 저장을 보류합니다. "
                                         + " / ".join(issue.message for issue in errors[:5]))
            return tuple(build_save_request(draft, f"{request_id}:{index}", reference, batch_drafts=drafts)
                         for index, draft in enumerate(drafts, 1))


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
