"""Excel 가져오기 검토·저장 요청 형식 (명세 BE-006~BE-009, 작업 C-1~C-3).

- 원본 셀 값·주소(:class:`SheetSnapshot`)와 AI 추출값(:attr:`ReviewRow.original`)은 바꾸지 않는다.
- 사용자가 고친 값은 :attr:`ReviewRow.edits`에 따로 두고, 저장할 때 ``edited_fields``로 남긴다.
- 저장은 :class:`ImportSaver` 형식의 서비스에 요청한다. 화면은 SQL을 실행하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from setron.features.ai.schemas import EQUIPMENT_STATUS_FIELDS, RELEASE_NOTE_FIELDS, ExtractKind, ReviewNote
from setron.infrastructure.ai_client import ResponseSource

FIELDS = {ExtractKind.EQUIPMENT_STATUS: EQUIPMENT_STATUS_FIELDS, ExtractKind.RELEASE_NOTE: RELEASE_NOTE_FIELDS}

FIELD_LABELS = {
    "base_date": "기준일", "site": "Site", "line": "Line(원본)", "group_name": "설비군",
    "model_name": "모델명", "production_area": "Memory/Foundry", "equipment_code": "설비 호기",
    "pm_name": "PM", "family_name": "SW 계열", "version_raw": "SW 버전 원문",
    "release_date": "출시일", "item_no": "Item 번호", "content": "변경 내용", "target_models": "대상 모델",
    "ui_type": "UI 구분", "structure_type": "구조 구분", "geometry_type": "형상",
    "modification_type": "개조 정보", "modification_mark": "개조 적용 표식",
    "engine_version": "Engine 버전", "epd_version": "EPD 버전",
}

KIND_LABELS = {ExtractKind.EQUIPMENT_STATUS: "설비 현황", ExtractKind.RELEASE_NOTE: "릴리즈 노트"}


class IssueLevel(str, Enum):
    ERROR = "ERROR"  # 하나라도 남으면 묶음 전체를 저장하지 않는다.
    REVIEW = "REVIEW"  # 사용자 확인이 필요하지만 저장을 막지 않는다.
    INFO = "INFO"


ISSUE_LABELS = {IssueLevel.ERROR: "오류", IssueLevel.REVIEW: "확인 필요", IssueLevel.INFO: "안내"}


@dataclass(frozen=True)
class Issue:
    level: IssueLevel
    message: str
    row_id: int | None = None
    field: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class SheetSnapshot:
    """읽은 시점의 시트 원본. ``cells``는 빈 칸을 뺀 ``주소: 값``이며 병합 범위는 왼쪽 위 칸에만 값이 있다."""

    sheet_name: str
    first_row: int
    first_col: int
    row_count: int
    col_count: int
    cells: dict[str, Any]
    merged: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewRow:
    """추출 행 하나. ``original``은 AI 추출값 그대로, ``edits``는 사용자가 고친 값이다."""

    row_id: int
    original: dict[str, str | None]
    sources: dict[str, str] = field(default_factory=dict)
    edits: dict[str, str | None] = field(default_factory=dict)
    excluded: bool = False

    def value(self, name: str) -> str | None:
        return self.edits[name] if name in self.edits else self.original.get(name)

    @property
    def edited_fields(self) -> tuple[str, ...]:
        return tuple(sorted(self.edits))


@dataclass(frozen=True)
class ImportDraft:
    kind: ExtractKind
    workbook_name: str
    sheet: SheetSnapshot
    rows: tuple[ReviewRow, ...]
    notes: tuple[ReviewNote, ...] = ()
    ai_source: ResponseSource | None = None

    @property
    def fields(self) -> tuple[str, ...]:
        return FIELDS[self.kind]

    @property
    def active_rows(self) -> tuple[ReviewRow, ...]:
        return tuple(row for row in self.rows if not row.excluded)

    def row(self, row_id: int) -> ReviewRow:
        for row in self.rows:
            if row.row_id == row_id:
                return row
        raise LookupError(f"추출 행 {row_id}이(가) 없습니다.")


@dataclass(frozen=True)
class SaveRow:
    """저장할 행. ``row_ids``는 정확한 중복을 합친 검토 행 번호다."""

    row_ids: tuple[int, ...]
    values: dict[str, str | None]
    source_reference: dict[str, dict[str, str]] | None
    edited_fields: tuple[str, ...]


@dataclass(frozen=True)
class SaveRequest:
    """한 번의 저장 묶음. ``workbook_name``은 확인창 표시용이며 ``raw_data``에 넣지 않는다."""

    request_id: str
    kind: ExtractKind
    workbook_name: str
    raw_data: dict[str, Any]
    rows: tuple[SaveRow, ...]
    review_count: int
    ai_source: ResponseSource | None


@dataclass(frozen=True)
class SaveReceipt:
    batch_id: int
    row_count: int
    saved_at: datetime
    target_label: str


class ImportBlockedError(ValueError):
    """오류가 남아 있어 저장 요청을 만들 수 없다."""


class DuplicateSaveError(Exception):
    """같은 저장 요청을 다시 보냈다."""


class ImportSaver(Protocol):
    """저장 서비스 형식. 묶음 전체를 저장하거나 전체를 취소하고, 실패는 예외로 알린다.

    DB 연결 오류는 :mod:`setron.infrastructure.database`의 오류 형식을 따른다.
    """

    target_label: str

    def save(self, request: SaveRequest) -> SaveReceipt: ...
