"""BE-006 SheetData → BE-005 AI 호출 → BE-007 검토 자료. DB를 변경하지 않는다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
import re

from setron.features.ai.schemas import (
    AIResult, AIStatus, AITask, EQUIPMENT_STATUS_FIELDS, RELEASE_NOTE_FIELDS, ExtractKind,
)
from setron.features.ai.service import extract_sheet
from setron.infrastructure.ai_client import ChatClient
from setron.infrastructure.excel_reader import SheetData, cell_address

from .normalization import normalize
from .schemas import CellSource, ExtractionIssue, ExtractionReview, ReviewRow


def extract_sheet_data(client: ChatClient, kind: ExtractKind, sheet: SheetData) -> AIResult[ExtractionReview]:
    """선택한 시트의 값·병합 위치를 AI에 보내고 출처를 대조한 검토 자료를 반환한다.

    한 번에 한 시트를 처리하므로 같은 통합문서의 C3 구UI·통합UI도 별개 결과로 유지한다.
    API 실패·제한·잘못된 JSON은 기존 AIResult 상태로 전달하며 자동 재시도하지 않는다.
    정상 응답은 검토 자료 생성 성공을 뜻한다. 저장 가능 판정은 BE-008에서 수행한다.
    """
    kind = ExtractKind(kind)
    # AI 호출 도중 호출자가 SheetData를 수정해도 요청과 대조 대상이 달라지지 않는다.
    sheet = deepcopy(sheet)
    originals = {sheet.address_of(i, j): value for i, row in enumerate(sheet.values) for j, value in enumerate(row)}
    cells = [(address, _text(value)) for address, value in originals.items() if value is not None and value != ""]
    if not cells:
        review = _review(sheet, kind, (), (ExtractionIssue("선택한 시트에 추출할 값이 없습니다."),), originals)
        return AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "빈 시트를 확인했습니다.", data=review)
    response = extract_sheet(client, kind, sheet.sheet_name, cells,
                             merged_ranges=[rng.address for rng in sheet.merged_ranges])
    if not response.ok:
        return replace(response, data=None)

    fields = EQUIPMENT_STATUS_FIELDS if kind is ExtractKind.EQUIPMENT_STATUS else RELEASE_NOTE_FIELDS
    components = _components(sheet) if kind is ExtractKind.EQUIPMENT_STATUS else {}
    issues = [ExtractionIssue(note.reason, note.field, note.address) for note in response.data.review_notes]
    rows = []
    for number, row in enumerate(response.data.rows, 1):
        values = dict.fromkeys(fields)
        raw_values = dict(row.values)
        sources = {}
        row_issues = []
        for field in fields:
            extracted = row.values.get(field)
            address = row.sources.get(field)
            if extracted is None and not address:
                continue
            source = _source(sheet, originals, address)
            if source is None:
                row_issues.append(ExtractionIssue("원본 셀 위치가 없거나 읽은 범위를 벗어났습니다.", field, address, number))
                continue
            sources[field] = source
            if extracted is None:
                continue
            source_text = _text(source.value)
            # 공백만 축약된 응답은 실제 셀의 원문을 복원한다. 부분 추출은 원문 안에 있어야 한다.
            if source_text.strip() == extracted.strip():
                extracted = source_text
            elif not _matches(field, extracted, source_text, row.values):
                row_issues.append(ExtractionIssue("추출값을 원본 셀에서 확인할 수 없습니다.", field, source.address, number))
                continue
            if source_text.startswith(("#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!")):
                row_issues.append(ExtractionIssue("원본 셀이 Excel 오류값입니다.", field, source.address, number))
                continue
            if field == "version_raw" and components.get(source.address) in {"ENGINE", "EPD"}:
                row_issues.append(ExtractionIssue("Engine·EPD 열의 값은 SW 버전으로 사용할 수 없습니다.", field, source.address, number))
                continue
            values[field] = extracted
        normalized, key, notes = normalize(values, kind, number, sources)
        row_issues.extend(notes)
        rows.append(ReviewRow(normalized, raw_values, sources, key, tuple(row_issues)))
        issues.extend(row_issues)
    if not rows:
        issues.append(ExtractionIssue("AI 응답에 추출된 행이 없습니다. 원본 양식과 응답을 확인하세요."))
    review = _review(sheet, kind, tuple(rows), tuple(issues), originals)
    return replace(response, data=review, message="추출 결과를 원본과 대조했습니다. 검토 후 검증을 진행하세요.")


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def _matches(field, extracted, source_text, row_values):
    if not extracted or extracted not in source_text:
        return False
    if field in {"equipment_code", "engine_version", "epd_version", "content"}:
        return extracted.strip() == source_text.strip()
    if field == "version_raw":
        # 릴리즈의 '버전 (출시일)'만 분리한다. 핫픽스·(H)·접미사를 잘라낸 값은 거부한다.
        version = re.sub(r"\s*\(\d{4}[-./]\d{1,2}[-./]\d{1,2}\)\s*$", "", source_text)
        if extracted.strip() == version.strip():
            return True
        # 혼합형의 'O3: 버전 / O2: 버전'은 해당 모델에 명시된 완전한 값만 허용한다.
        # 임의 부분 문자열이나 Engine 값, 계열 연결이 없는 값으로 대체하지 않는다.
        model = row_values.get("model_name") or row_values.get("family_name") or ""
        target = re.fullmatch(r"(?:M-)?(O[235])", model.strip())
        if not target:
            return False
        labeled = [re.fullmatch(r"(?:M-)?(O[235])\s*:\s*(.+)", part.strip())
                   for part in source_text.split("/")]
        if not all(labeled):
            return False
        candidates = [part[2] for part in labeled if part[1] == target[1]]
        return len(candidates) == 1 and extracted.strip() == candidates[0].strip()
    if field == "model_name" and "Prime" in source_text and "Prime" not in extracted:
        return False
    return True


def _source(sheet, originals, address):
    if not isinstance(address, str):
        return None
    match = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})", address.strip())
    if not match:
        return None
    letters, row = match.groups()
    col = 0
    for letter in letters.upper():
        col = col * 26 + ord(letter) - ord("A") + 1
    row = int(row)
    if row > 1048576 or col > 16384:
        return None
    canonical = cell_address(row, col)
    if canonical not in originals:
        return None
    for merged in sheet.merged_ranges:
        if merged.contains(row, col):
            canonical = cell_address(merged.top, merged.left)
            break
    if canonical not in originals:
        return None
    return CellSource(sheet.sheet_name, canonical, deepcopy(originals[canonical]))


def _components(sheet):
    """머리글 위치를 한 번 읽어 같은 열 위쪽·같은 행 왼쪽의 부품 구분을 찾는다."""
    headers = {"engine": "ENGINE", "engine version": "ENGINE", "engine ver": "ENGINE",
               "epd": "EPD", "epd version": "EPD", "epd ver": "EPD",
               "sw": "SW", "version": "SW", "sw version": "SW", "sw ver": "SW",
               "sw버전": "SW", "engine버전": "ENGINE", "epd버전": "EPD", "s/w": "SW",
               "s/w version": "SW", "s/w ver": "SW", "sw 버전": "SW",
               "engine 버전": "ENGINE", "epd 버전": "EPD"}
    columns, result = {}, {}
    for i, row in enumerate(sheet.filled_values()):
        left = None
        for j, value in enumerate(row):
            candidates = []
            if j in columns:
                r, component = columns[j]
                candidates.append((i - r, component == "SW", component))
            if left:
                c, component = left
                candidates.append((j - c, component == "SW", component))
            if candidates:
                result[sheet.address_of(i, j)] = min(candidates)[2]
            header = re.sub(r"[._\s]+", " ", _text(value)).strip().casefold()
            if header in headers:
                columns[j] = (i, headers[header])
                left = (j, headers[header])
    return result


def _review(sheet, kind, rows, issues, originals):
    # 기존 릴리즈 원본 조회가 사용하는 {시트명: {주소: 값}} 형식이다.
    # Excel 날짜만 JSON 문자열로 바꾸고 숫자·False·빈 값과 주소는 그대로 보존한다.
    raw = {address: _text(value) if isinstance(value, (date, datetime)) else deepcopy(value)
           for address, value in originals.items()}
    return ExtractionReview(kind, sheet.workbook_name, sheet.sheet_name, rows, issues,
                            {sheet.sheet_name: raw})
