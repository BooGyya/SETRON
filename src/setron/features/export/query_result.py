"""AI 분석 조회 결과를 Excel로 내보낸다 (명세 BE-021, 화면 10-1 "Excel 내보내기").

화면에서 고르는 것
- 내보낼 범위: 조회 결과(답변 표에 보인 설비) / 같은 조건의 전체 설비(목표 달성 설비까지)
- 포함할 열: 설비명 · 원본 구역 · 설치 버전 · 목표 N · 판정 · 원본 출처
- 요청 인식 조건: 고르면 두 번째 시트 "조회 조건"에 질문과 조건을 적는다

행은 설비 1대 × 계열 1개다(적용률 계산 1건과 같음). HPA는 공통 집계와 같이 넣지 않는다.
원본 출처는 저장된 시트·셀과 입력 작업 번호다. 통합문서 파일 이름은 DB에 저장하지 않아 적지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.styles import Font

from setron.features.aggregation.schemas import Summary
from setron.features.equipment.schemas import StoredSnapshot
from setron.features.export.schemas import ExportResult, ExportStatus
from setron.features.export.service import _family_label, _target, _versions
from setron.features.export.xlsx_cells import ReportTextWriter

RESULT_SHEET, CONDITION_SHEET = "조회 결과", "조회 조건"


class QueryColumn(str, Enum):
    EQUIPMENT = "설비명"
    LOCATION = "원본 구역"
    VERSION = "설치 버전"
    TARGET = "목표 N"
    JUDGMENT = "판정"
    SOURCE = "원본 출처"


ALL_COLUMNS = tuple(QueryColumn)


class ExportRange(str, Enum):
    SHOWN = "조회 결과"          # 답변 표에 보인 설비만
    ALL = "같은 조건의 전체 설비"  # 목표 달성 설비까지


@dataclass(frozen=True, kw_only=True)
class QueryResultExport:
    """내보낼 AI 조회 결과.

    - ``summary``: 같은 조건으로 고른 전체 설비의 판정(BE-012). HPA는 이미 빠져 있다.
    - ``shown_ids``: 답변 표에 보인 설비 ID. None이면 전체와 같다.
    - ``conditions``: 요청 인식 조건 (이름, 값). 예: ("Site", "평택"), ("설비군", "M-O2").
    - ``sources``: (설비 ID, 계열 ID) → 원본 출처 문구. :func:`source_labels`로 만든다.
    """

    summary: Summary
    question: str = ""
    conditions: tuple[tuple[str, str], ...] = ()
    shown_ids: tuple[int, ...] | None = None
    family_names: Mapping[int, str] = field(default_factory=dict)
    sources: Mapping[tuple[int, int], str] = field(default_factory=dict)
    created_at: datetime | None = None


def range_counts(export: QueryResultExport) -> dict[ExportRange, int]:
    """범위별 설비 대수. 화면 선택지의 "조회 결과 3대 / 같은 조건의 전체 설비 8대"에 쓴다."""
    return {scope: len(_equipment(export, scope)) for scope in ExportRange}


def default_filename(export: QueryResultExport) -> str:
    """기본 파일 이름: AI조회_{조건 값}_{기준일}.xlsx. 파일 이름에 쓸 수 없는 글자는 뺀다."""
    parts = ["AI조회", *(value for _, value in export.conditions)]
    if export.summary.base_date:
        parts.append(export.summary.base_date.isoformat())
    name = "_".join(re.sub(r'[\\/:*?"<>|\s]+', "", part) for part in parts if part and part.strip())
    return f"{name[:100] or 'AI조회'}.xlsx"


def build_query_result(export: QueryResultExport, *, scope: ExportRange = ExportRange.SHOWN,
                       columns: Sequence[QueryColumn] = ALL_COLUMNS, include_conditions: bool = True) -> Workbook:
    """조회 결과 시트(와 조회 조건 시트)를 만든다. 열은 ``columns`` 순서가 아니라 화면 순서로 둔다."""
    chosen = tuple(c for c in ALL_COLUMNS if c in set(columns))
    if not chosen:
        raise ValueError("포함할 열을 하나 이상 골라 주세요.")
    book = Workbook()
    text = ReportTextWriter(book)
    sheet = book.active
    sheet.title = RESULT_SHEET
    rows = _rows(export, scope)
    for column, name in enumerate(chosen, start=1):
        cell = sheet.cell(1, column)
        text.write(cell, name.value, name.value)
        cell.font = Font(bold=True)
    for offset, row in enumerate(rows, start=2):
        for column, name in enumerate(chosen, start=1):
            text.write(sheet.cell(offset, column), row[name], name.value)
    sheet.freeze_panes = "A2"
    for column, name in enumerate(chosen, start=1):
        sheet.column_dimensions[sheet.cell(1, column).column_letter].width = 28 if name is QueryColumn.SOURCE else 16
    if include_conditions:
        _conditions(book.create_sheet(CONDITION_SHEET), export, scope, chosen, len(rows), text)
    text.finish()
    return book


def save_query_result(export: QueryResultExport, path: str | Path, *, scope: ExportRange = ExportRange.SHOWN,
                      columns: Sequence[QueryColumn] = ALL_COLUMNS, include_conditions: bool = True) -> ExportResult:
    """파일로 저장한다. 같은 폴더의 임시 파일에 쓴 뒤 바꿔서, 실패하면 기존 파일을 그대로 둔다."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        return ExportResult(ExportStatus.FAILED, path, "파일 이름은 .xlsx로 끝나야 합니다.")
    temporary = None
    try:
        book = build_query_result(export, scope=scope, columns=columns, include_conditions=include_conditions)
        with NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
        book.save(temporary)
        book.close()
        os.replace(temporary, path)
    except PermissionError:
        return ExportResult(ExportStatus.FAILED, path, "파일을 저장할 수 없습니다. 같은 이름의 파일이 Excel에서 열려 있는지, "
                                                       "폴더 쓰기 권한이 있는지 확인해 주세요.")
    except OSError as error:
        return ExportResult(ExportStatus.FAILED, path, f"파일을 저장하지 못했습니다: {error.strerror or error}")
    except ValueError as error:
        return ExportResult(ExportStatus.FAILED, path, str(error))
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return ExportResult(ExportStatus.SAVED, path)


def source_labels(snapshots: Iterable[StoredSnapshot]) -> dict[tuple[int, int], str]:
    """저장된 현황에서 (설비 ID, 계열 ID)별 원본 출처 문구를 만든다. 예: "Memory · H87 · 입력 작업 18"."""
    labels = {}
    for snapshot in snapshots:
        for software in snapshot.software:
            reference = (software.source_reference or {}).get("version_raw") or {}
            cells = ", ".join(reference.get("cells") or ())
            parts = [reference.get("sheet"), cells, f"입력 작업 {software.batch_id}"]
            labels[snapshot.equipment_id, software.family_id] = " · ".join(p for p in parts if p)
    return labels


# --- 내부 ----------------------------------------------------------------------

def _equipment(export: QueryResultExport, scope: ExportRange):
    statuses = export.summary.equipment
    if scope is ExportRange.ALL or export.shown_ids is None:
        return statuses
    shown = set(export.shown_ids)
    return tuple(e for e in statuses if e.record.equipment_id in shown)


def _rows(export: QueryResultExport, scope: ExportRange) -> list[dict[QueryColumn, str]]:
    rows = []
    for equipment in sorted(_equipment(export, scope),
                            key=lambda e: (e.record.site, e.record.line or "", e.record.equipment_code)):
        record = equipment.record
        for family in equipment.families:
            name = export.family_names.get(family.family_id, f"계열 {family.family_id}")
            rows.append({
                QueryColumn.EQUIPMENT: record.equipment_code,
                QueryColumn.LOCATION: " · ".join(p for p in (record.site, record.line) if p),
                QueryColumn.VERSION: f"{name} {_versions(family)}",
                QueryColumn.TARGET: _target(family),
                QueryColumn.JUDGMENT: _family_label(family),
                QueryColumn.SOURCE: export.sources.get((record.equipment_id, family.family_id), "출처 없음"),
            })
    return rows


def _conditions(sheet, export: QueryResultExport, scope: ExportRange, columns: tuple[QueryColumn, ...],
                count: int, text: ReportTextWriter) -> None:
    counts = range_counts(export)
    created = export.created_at or datetime.now()
    rows = [
        ("질문", export.question or "(질문 없음)"),
        *(("요청 인식 · " + name, value) for name, value in export.conditions),
        ("기준일", export.summary.base_date.isoformat() if export.summary.base_date else "기준일 없음"),
        ("내보낸 범위", f"{scope.value} {counts[scope]}대 ({count}행 · 설비 1대 × 계열 1개)"),
        ("같은 조건의 전체 설비", f"{counts[ExportRange.ALL]}대 · HPA {export.summary.hpa_excluded}대 제외"),
        ("포함한 열", " · ".join(c.value for c in columns)),
        ("판정 기준", "모델·계열별 목표 N과 같은 계열의 릴리즈 순서로 비교한 내보낸 시점의 결과입니다."),
        ("생성 시각", created.strftime("%Y-%m-%d %H:%M")),
    ]
    for number, (name, value) in enumerate([("항목", "값"), *rows], start=1):
        text.write(sheet.cell(number, 1), name, "항목")
        text.write(sheet.cell(number, 2), value, "값")
        if number == 1:
            sheet.cell(number, 1).font = sheet.cell(number, 2).font = Font(bold=True)
    sheet.column_dimensions["A"].width = 24
    sheet.column_dimensions["B"].width = 70
