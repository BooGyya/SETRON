"""위치가 불명확한 원본 셀을 사용자가 지정하고 검증한다."""

from dataclasses import replace

from setron.features.ai.schemas import ExtractKind
from setron.infrastructure.excel_reader import cell_address
from setron.features.extraction.service import recheck_hpa_coverage
from .schemas import Issue, IssueLevel
from .source_sheet import sheet_from_snapshot
from .service import in_sheet, parse_address, value_at


def refresh_hpa_coverage(draft):
    """설비의 현재 검토값·출처로 HPA 누락을 다시 검사하고 다른 오류는 유지한다.

    param
    - draft: 값을 보완하거나 원본 위치를 확정한 검토 초안.

    return
    - 재검사 결과를 포함한 초안. 릴리즈와 미대조 수동 초안은 변경하지 않음.
    """
    global_codes = {"HPA_COVERAGE_MISSING", "HPA_COVERAGE_UNRESOLVED"}
    if draft.kind is not ExtractKind.EQUIPMENT_STATUS:
        return draft
    if not any(row.normalized for row in draft.rows) and not any(issue.code in global_codes for issue in draft.extraction_issues):
        return draft
    notes = recheck_hpa_coverage(sheet_from_snapshot(draft.workbook_name, draft.sheet),
        tuple(({name: item.value(name) for name in draft.fields}, item.sources) for item in draft.rows))
    retained = tuple(issue for issue in draft.extraction_issues if issue.code not in global_codes)
    refreshed = tuple(Issue(IssueLevel.ERROR if note.blocks_save else IssueLevel.REVIEW, note.reason,
                            note.row_number, note.field, note.address, code=note.code) for note in notes)
    issues = retained + refreshed
    return draft if issues == draft.extraction_issues else replace(draft, extraction_issues=issues)


def assign_source(draft, row_id, field, address):
    """미확정 원본 위치를 사용자가 확인한 범위 안의 셀로 지정한다.

    param
    - draft, row_id: 원문을 보존한 검토 초안과 추출 행 ID.
    - field, address: 검사할 필수 항목과 사용자가 확인한 원본 셀 주소.

    return
    - 원본 값은 변경하지 않고 확인한 위치를 포함한 새 초안.

    raises
    - ValueError: 지원하지 않는 항목·범위 밖 주소이거나 확정 위치를 변경하려는 경우.
    """
    if field not in {"equipment_code", "version_raw"}:
        raise ValueError("호기 또는 SW 버전의 원본 셀을 지정해 주세요.")
    row = draft.row(row_id)
    top, left = parse_address(address)
    if top > 1048576 or left > 16384:
        raise ValueError("Excel에서 사용할 수 있는 원본 셀 주소를 지정해 주세요.")
    canonical = cell_address(top, left)
    if not in_sheet(draft.sheet, canonical):
        raise ValueError("읽은 원본 시트 범위 안의 셀을 지정해 주세요.")
    existing = row.sources.get(field)
    if existing and in_sheet(draft.sheet, existing) and existing != canonical:
        raise ValueError("이미 확인된 원본 위치는 변경할 수 없습니다. 원본을 다시 읽고 추출해 주세요.")
    # 병합 셀의 원본은 앵커다. 공란을 다른 행의 값으로 채우지 않는다.
    for area in draft.sheet.merged:
        (first_row, first_col), (last_row, last_col) = (parse_address(part) for part in area.split(":"))
        if first_row <= top <= last_row and first_col <= left <= last_col:
            canonical = cell_address(first_row, first_col)
            break
    cells = dict(draft.sheet.cells)
    cells.setdefault(canonical, value_at(draft.sheet, canonical))
    updated = replace(row, sources={**row.sources, field: canonical})
    result = replace(draft, sheet=replace(draft.sheet, cells=cells),
                     rows=tuple(updated if item.row_id == row_id else item for item in draft.rows))
    return refresh_hpa_coverage(result)
