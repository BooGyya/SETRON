"""Excel 가져오기 검토·저장 요청 형식 (명세 BE-006~BE-009, 작업 C-1~C-3).

- 원본 셀 값·주소(:class:`SheetSnapshot`)와 AI 추출값(:attr:`ReviewRow.original`)은 바꾸지 않는다.
- 사용자가 고친 값은 :attr:`ReviewRow.edits`에 따로 두고, 저장할 때 ``edited_fields``로 남긴다.
- 저장은 :class:`ImportSaver` 형식의 서비스에 요청한다. 화면은 SQL을 실행하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import re
from typing import Any, Protocol

from setron.features.ai.schemas import EQUIPMENT_STATUS_FIELDS, RELEASE_NOTE_FIELDS, ExtractKind, ReviewNote
from setron.infrastructure.ai_client import ResponseSource
from setron.features.version.schemas import VersionComparison
from setron.features.equipment.schemas import SnapshotInput
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from setron.features.release.refresh_schemas import RefreshContext, ReleaseRefresh
    from setron.features.release.replacement_schemas import ReplacementReview

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
    """검토 화면에 전달할 검증 결과 데이터.

    ivar
    - level, message: 저장 차단 수준과 사용자 안내 사유.
    - row_id: Excel 행 번호와 구분하는 추출 행 ID. 전체 문제는 None.
    - field, address: 문제 항목과 원본 셀 주소. 위치를 모르면 None.
    - workbook_name, sheet_name: 확인창용 파일명과 원본 시트명.
    - code: 화면 문구와 독립적인 검증 결과 코드.
    - previous_value, current_value: 기존 저장값과 가져온 값. 변경 비교가 아니면 None.
    - comparison: 등록 릴리즈에 근거한 N 비교 결과. 비교 대상이 아니면 None.
    - workbook_id: 동명 통합문서를 구분할 임시 식별자. DB에 저장하지 않음.
    """

    level: IssueLevel
    message: str
    row_id: int | None = None
    field: str | None = None
    address: str | None = None
    workbook_name: str | None = None
    sheet_name: str | None = None
    code: str | None = None
    previous_value: Any = None
    current_value: Any = None
    comparison: VersionComparison | None = None
    workbook_id: str | None = None


    @property
    def excel_position(self) -> tuple[int, int] | None:
        """원본 주소를 Excel의 행·열 번호로 변환한다.

        return
        - 1부터 시작하는 (행, 열). 주소가 없거나 잘못됐으면 None.
        """
        match = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)", (self.address or "").split(":")[0])
        if match is None:
            return None
        column = 0
        for letter in match[1].upper():
            column = column * 26 + ord(letter) - ord("A") + 1
        return int(match[2]), column

    @property
    def blocks_save(self) -> bool:
        """이번 저장 묶음을 보류해야 하는지 반환한다.

        return
        - 필수값·형식·충돌 등의 ERROR이면 True. 단순 안내와 확인 항목은 False.
        """
        return self.level is IssueLevel.ERROR


@dataclass(frozen=True)
class SheetSnapshot:
    """읽은 시점의 시트 원본. ``cells``는 읽은 빈칸도 None으로 보존하며 병합값은 왼쪽 위 칸에만 있다."""

    sheet_name: str
    first_row: int
    first_col: int
    row_count: int
    col_count: int
    cells: dict[str, Any]
    merged: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewRow:
    """원문·정규값·수정값을 분리한 검토 행 데이터.

    ivar
    - row_id: Excel 행 번호와 구분하는 추출 행 ID.
    - original: AI 추출 원문. 이후 수정으로 덮어쓰지 않음.
    - sources: 항목별 원본 셀 주소.
    - edits: 사용자가 고친 현재 값.
    - excluded: 사용자가 이번 저장 대상에서 제외했는지 여부.
    - normalized: BE-007에서 원본을 대조하고 확정 규칙을 적용한 값. 원문과 별도로 보존.
    """

    row_id: int
    original: dict[str, str | None]
    sources: dict[str, str] = field(default_factory=dict)
    edits: dict[str, str | None] = field(default_factory=dict)
    excluded: bool = False
    normalized: dict[str, str | None] = field(default_factory=dict)

    def value(self, name: str) -> str | None:
        """수정값·확정 정규값·AI 추출 원문 순으로 현재 값을 조회한다.

        param
        - name: 조회할 공통 항목 이름.

        return
        - 화면과 검증에 사용할 현재 값. 값이 없으면 None.
        """
        return self.edits[name] if name in self.edits else self.normalized.get(name, self.original.get(name))

    @property
    def edited_fields(self) -> tuple[str, ...]:
        return tuple(sorted(self.edits))


@dataclass(frozen=True)
class ImportDraft:
    """한 파일·시트의 수정·재검증 초안 데이터.

    ivar
    - kind: 설비 현황 또는 릴리즈 노트.
    - workbook_name: 확인창과 문제 위치에 표시할 파일명. DB 원본 데이터에는 추가하지 않음.
    - sheet: 읽은 시점의 원본 셀과 위치.
    - rows: 원문·정규값·수정값을 가진 전체 행. 제외 행도 보존.
    - notes: 기존 AI 추출기의 전체 확인 메모.
    - ai_source: 예제·실제 AI 응답 구분. 미확인은 None.
    - extraction_issues: BE-007 원본 대조 결과. 현재 수정값과 함께 재검증.
    - workbook_id: 경로·Excel 프로세스가 다른 동명 파일을 구분할 임시 식별자. DB에 저장하지 않음.
    """

    kind: ExtractKind
    workbook_name: str
    sheet: SheetSnapshot
    rows: tuple[ReviewRow, ...]
    notes: tuple[ReviewNote, ...] = ()
    ai_source: ResponseSource | None = None
    extraction_issues: tuple[Issue, ...] = ()
    workbook_id: str | None = None

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
    """중복을 정리한 저장 요청 행 데이터.

    ivar
    - row_ids: 정확한 중복을 합친 검토 행 ID 목록.
    - values: 현재 저장할 공통 항목. Item 번호는 정수, 미확인 선택값은 None.
    - source_reference: 공통 저장 서비스가 받는 필드별 시트·셀 주소 목록.
    - edited_fields: 원본·확정 정규값과 다르게 수정한 항목 이름.
    - family_id: 검증에서 유일하게 확인한 계열 ID. 기준 자료가 미연결이면 None.
    - release_version_id: 원문 또는 확정 별칭으로 연결한 릴리즈 ID. 미등록·미연결은 None.
    """

    row_ids: tuple[int, ...]
    values: dict[str, Any]
    source_reference: dict[str, dict[str, Any]] | None
    edited_fields: tuple[str, ...]
    family_id: int | None = None
    release_version_id: int | None = None


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
    # None은 DB 기존값 검토 전, 빈 튜플은 검토 당시 해당 날짜 현황이 없었음을 뜻한다.
    expected_snapshots: tuple[SnapshotInput, ...] | None = None
    # 파일명과 별도로 같은 통합문서의 시트들을 묶는 임시 식별자. DB 원본에는 넣지 않는다.
    workbook_id: str | None = None
    # 전체 누적 노트·파일명 확인과 계열별 충돌 선택은 이번 요청에만 사용한다.
    release_note_confirmed: bool = False
    filename_confirmed: bool = False
    release_reviews: tuple[ReplacementReview, ...] = ()
    refresh_context: RefreshContext | None = None


@dataclass(frozen=True)
class SaveReceipt:
    batch_id: int
    row_count: int
    saved_at: datetime
    target_label: str
    refresh: ReleaseRefresh | None = None


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


@dataclass(frozen=True)
class ImportComparison:
    """같은 시트·Site 범위로 확인한 직전 저장본 비교의 표시 계약. 숫자를 UI에서 추정하지 않는다."""

    previous_date: date
    scope_label: str
    changed: int
    added: int
    unchanged: int
    absent: int
    details: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.previous_date, date) or not isinstance(self.scope_label, str) or not self.scope_label.strip():
            raise ValueError("비교 기준일과 범위가 필요합니다.")
        if any(type(v) is not int or v < 0 for v in (self.changed, self.added, self.unchanged, self.absent)):
            raise ValueError("비교 대수는 0 이상의 정수여야 합니다.")
        if not isinstance(self.details, tuple) or any(not isinstance(t, str) for t in self.details):
            raise ValueError("비교 상세 형식이 올바르지 않습니다.")
