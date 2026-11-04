"""BE-006 SheetData → BE-005 AI 호출 → BE-007 검토 자료. DB를 변경하지 않는다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import re
import sys

from setron.features.ai.schemas import (
    AIResult, AIStatus, AITask, EQUIPMENT_STATUS_FIELDS, RELEASE_NOTE_FIELDS, ExtractKind, ExtractResult,
)
from setron.features.ai.service import extract_sheet
from setron.infrastructure.ai_client import ChatClient
from setron.infrastructure.excel_reader import SheetData, cell_address

from .normalization import normalize
from .locations import (
    equipment_coverage_issues, equipment_header_rows, hpa_coverage_issues,
    match_location, record_locations, restore_required_sources,
)
from .schemas import CellSource, ExtractionIssue, ExtractionProgress, ExtractionReview, ReviewRow

RELEASE_CHUNK_ROWS = 20
EQUIPMENT_CHUNK_CELLS = 120


def extract_sheet_data(client: ChatClient, kind: ExtractKind, sheet: SheetData, *,
                       progress=None, cancelled=None, max_workers=1) -> AIResult[ExtractionReview]:
    """선택한 시트의 값·병합 위치를 AI에 보내고 출처를 대조한 검토 자료를 반환한다.

    한 번에 한 시트를 처리하므로 같은 통합문서의 C3 구UI·통합UI도 별개 결과로 유지한다.
    API 실패·제한·잘못된 JSON은 기존 AIResult 상태로 전달하며 자동 재시도하지 않는다.
    정상 응답은 검토 자료 생성 성공을 뜻한다. 저장 가능 판정은 BE-008에서 수행한다.

    param
    - client: 원본 시트 추출을 요청할 AI 호출 클라이언트.
    - kind: 설비 현황 또는 릴리즈 노트.
    - sheet: 파일·시트명과 실제 읽은 셀·병합 범위를 포함한 SheetData.
    - progress: 구간 응답 수신과 원본 대조 단계의 보고 함수. 위젯에 직접 접근하지 않아야 함.
    - cancelled: 협력 취소 여부를 반환하는 함수.
    - max_workers: 동시에 보낼 릴리즈 추출 요청 수. 1 또는 2.

    return
    - 원본과 대조한 검토 자료 또는 AI 호출·응답 실패를 담은 AIResult.
    """
    kind = ExtractKind(kind)
    _check_cancelled(cancelled)
    if max_workers not in (1, 2):
        raise ValueError("동시 추출 요청은 1개 또는 2개만 허용합니다.")
    # AI 호출 도중 호출자가 SheetData를 수정해도 요청과 대조 대상이 달라지지 않는다.
    sheet = deepcopy(sheet)
    originals = {sheet.address_of(i, j): value for i, row in enumerate(sheet.values) for j, value in enumerate(row)}
    cells = [(address, _text(value)) for address, value in originals.items() if value is not None and value != ""]
    if not cells:
        review = _review(sheet, kind, (), (ExtractionIssue("선택한 시트에 추출할 값이 없습니다."),), originals)
        return AIResult(AITask.EXCEL_EXTRACT, AIStatus.OK, "빈 시트를 확인했습니다.", data=review)
    # 실제로 읽은 직사각형만 범위로 안내한다. 불완전한 합성 표의 미제공 셀을 빈칸으로 만들지 않는다.
    width = max((len(row) for row in sheet.values), default=0)
    used_range = None
    if width and all(len(row) == width for row in sheet.values):
        used_range = f"{sheet.address_of(0, 0)}:{sheet.address_of(sheet.row_count - 1, width - 1)}"
    options = dict(merged_ranges=[rng.address for rng in sheet.merged_ranges], used_range=used_range)
    if kind is ExtractKind.RELEASE_NOTE and sheet.row_count > RELEASE_CHUNK_ROWS:
        response = _extract_release_chunks(client, sheet, cells, options, progress, cancelled, max_workers)
    elif kind is ExtractKind.EQUIPMENT_STATUS and (bounds := _equipment_bounds(sheet, cells)):
        response = _extract_chunks(client, kind, sheet, cells, options, bounds, progress, cancelled, max_workers)
    else:
        if progress:
            progress(ExtractionProgress(0, 1))
        response = extract_sheet(client, kind, sheet.sheet_name, cells, **options)
        if progress and response.ok:
            progress(ExtractionProgress(1, 1))
    _check_cancelled(cancelled)
    if progress and response.ok:
        progress(ExtractionProgress(1, 1, "review"))
    return review_extraction_result(kind, sheet, response)


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise InterruptedError("AI 추출을 취소했습니다.")


def _release_context(sheet, cells):
    """세로형 표의 제목·머리말과 버전/날짜 열을 확인한다. 확인 불가 양식은 전체 문맥을 유지한다."""
    version_names = {"version", "버전", "버전명", "swversion", "sw버전"}
    content_names = {"description", "update내용", "업데이트내용", "변경내용", "변경사항", "내용"}
    date_names = {"date", "releasedate", "출시일", "배포일"}
    headers, shared_columns = [], set()
    for index, row in enumerate(sheet.values, sheet.first_row):
        names = [re.sub(r"[\s_./:-]", "", str(value or "").casefold()) for value in row]
        if set(names) & version_names and set(names) & content_names:
            headers.append(index)
            shared_columns.update(sheet.first_col + col for col, name in enumerate(names)
                                  if name in version_names | date_names)
    if not headers:
        return None
    prefix = {address for address, _ in cells if int(re.search(r"[0-9]+$", address)[0]) <= headers[0]}
    prefix.update(address for address, _ in cells if int(re.search(r"[0-9]+$", address)[0]) in headers)
    for merged in sheet.merged_ranges:
        if merged.left <= sheet.first_col and merged.right >= sheet.first_col + sheet.col_count - 1:
            prefix.add(cell_address(merged.top, merged.left))
    return prefix, shared_columns


def _release_chunk(sheet, cells, options, context, first, last):
    if context is None:
        return cells, options
    selected, shared_columns = set(context[0]), context[1]
    previous = {}
    for address, _ in cells:
        match = re.fullmatch(r"([A-Z]+)([0-9]+)", address)
        row = int(match[2])
        if first <= row <= last:
            selected.add(address)
        elif row < first:
            for column in shared_columns:
                if match[1] == cell_address(1, column)[:-1]:
                    previous[column] = address
    selected.update(previous.values())
    ranges = []
    for merged in sheet.merged_ranges:
        anchor = cell_address(merged.top, merged.left)
        if (merged.top <= last and merged.bottom >= first) or anchor in selected:
            selected.add(anchor)
            ranges.append(merged.address)
    bounds = f"{cell_address(first, sheet.first_col)}:{cell_address(last, sheet.first_col + sheet.col_count - 1)}"
    return [(address, value) for address, value in cells if address in selected], dict(
        merged_ranges=ranges, used_range=bounds)


def _equipment_bounds(sheet, cells):
    """확정된 세로형 현황표만 응답 크기가 비슷한 행 구간으로 나눈다. 문맥은 전체 보존한다."""
    headers = equipment_header_rows(sheet)
    if not headers or len(cells) <= EQUIPMENT_CHUNK_CELLS:
        return None
    weights = {}
    for address, _ in cells:
        row = int(re.search(r"[0-9]+$", address)[0])
        if row > headers[0]:
            weights[row] = weights.get(row, 0) + 1
    total = sum(weights.values())
    parts = min(len(weights), (len(cells) + EQUIPMENT_CHUNK_CELLS - 1) // EQUIPMENT_CHUNK_CELLS)
    if parts < 2:
        return None
    first, cumulative, bounds = sheet.first_row, 0, []
    last_row = sheet.first_row + sheet.row_count - 1
    for row, weight in sorted(weights.items()):
        cumulative += weight
        if len(bounds) < parts - 1 and cumulative * parts >= total * (len(bounds) + 1):
            bounds.append((first, row))
            first = row + 1
    if first <= last_row:
        bounds.append((first, last_row))
    return bounds


def _equipment_record_row(row, sheet):
    # 공유된 세로 병합 앵커는 현재 레코드의 행을 가리키지 않는다.
    shared = {cell_address(area.top, area.left) for area in sheet.merged_ranges if area.bottom > area.top}
    for name in ("version_raw", "equipment_code", "pm_name", "model_name"):
        address = row.sources.get(name, "")
        match = re.fullmatch(r"\$?([A-Za-z]+)\$?([1-9][0-9]*)", address.strip())
        if match and f"{match[1].upper()}{match[2]}" not in shared:
            return int(match[2])
    return None


def _extract_chunk(client, kind, name, cells, options, first, last, cancelled):
    uninitialize = None
    try:
        if sys.platform == "win32":
            import pythoncom
            pythoncom.CoInitialize()
            uninitialize = pythoncom.CoUninitialize
        _check_cancelled(cancelled)
        return extract_sheet(client, kind, name, cells, extract_rows=(first, last), **options)
    finally:
        if uninitialize:
            uninitialize()


def _extract_release_chunks(client, sheet, cells, options, progress, cancelled, max_workers):
    """최대 두 구간만 동시에 요청하고 응답 순서와 무관하게 원본 순서로 합친다. 일부 성공은 반환하지 않는다."""
    last_row = sheet.first_row + sheet.row_count - 1
    bounds = [(first, min(first + RELEASE_CHUNK_ROWS - 1, last_row))
              for first in range(sheet.first_row, last_row + 1, RELEASE_CHUNK_ROWS)]
    return _extract_chunks(client, ExtractKind.RELEASE_NOTE, sheet, cells, options, bounds,
                           progress, cancelled, max_workers, _release_context(sheet, cells))


def _extract_chunks(client, kind, sheet, cells, options, bounds, progress, cancelled, max_workers, context=None):
    results, pending = {}, {}
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="setron-extraction")
    next_index = 0
    if progress:
        progress(ExtractionProgress(0, len(bounds)))
    try:
        while pending or next_index < len(bounds):
            _check_cancelled(cancelled)
            while len(pending) < max_workers and next_index < len(bounds):
                _check_cancelled(cancelled)
                first, last = bounds[next_index]
                body, metadata = _release_chunk(sheet, cells, options, context, first, last)
                pending[executor.submit(_extract_chunk, client, kind, sheet.sheet_name, body, metadata,
                                        first, last, cancelled)] = next_index
                next_index += 1
            done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            _check_cancelled(cancelled)
            for future in done:
                index = pending.pop(future)
                first, last = bounds[index]
                response = future.result()
                if not response.ok:
                    return replace(response, data=None, message=f"{first}~{last}행 추출 실패 · {response.message}")
                for row in response.data.rows:
                    match = re.fullmatch(r"[A-Z]+([1-9][0-9]*)", row.sources.get("content", ""))
                    source_row = (int(match[1]) if match else None) if kind is ExtractKind.RELEASE_NOTE else _equipment_record_row(row, sheet)
                    if source_row is not None and not first <= source_row <= last:
                        return replace(response, status=AIStatus.INVALID_RESPONSE, data=None,
                                       message=f"{first}~{last}행 밖의 {'변경 항목' if kind is ExtractKind.RELEASE_NOTE else '설비 항목'}이 반환되었습니다. 다시 추출해 주세요.")
                results[index] = response
                if progress:
                    progress(ExtractionProgress(len(results), len(bounds)))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    rows = [row for index in range(len(bounds)) for row in results[index].data.rows]
    notes = [note for index in range(len(bounds)) for note in results[index].data.review_notes]
    data = ExtractResult(kind, sheet.sheet_name, tuple(rows), tuple(dict.fromkeys(notes)))
    return replace(response, data=data, raw_content=None)


def review_extraction_result(kind: ExtractKind, sheet: SheetData,
                             response: AIResult[ExtractResult]) -> AIResult[ExtractionReview]:
    """기본 AI와 주입한 추출 공급자 응답에 동일한 원본 대조·HPA 누락 검사를 적용한다.

    param
    - kind, sheet: 요청한 자료 종류와 읽은 시점의 원본 시트. 셀 값을 변경하지 않는다.
    - response: 원본 시트에 대응하는 추출 결과. 실패 응답은 자료 없이 그대로 전달한다.

    return
    - 필드별 원문·출처와 위치 미확인·누락 차단 사유를 가진 검토 결과.
    """
    kind = ExtractKind(kind)
    if not response.ok:
        return replace(response, data=None)
    if not isinstance(response.data, ExtractResult) or response.data.kind is not kind or response.data.sheet_name != sheet.sheet_name:
        return replace(response, status=AIStatus.INVALID_RESPONSE, data=None,
                       message="추출 결과의 자료 종류·시트가 읽은 원본과 다릅니다.")
    sheet = deepcopy(sheet)
    originals = {sheet.address_of(i, j): value for i, row in enumerate(sheet.values) for j, value in enumerate(row)}

    fields = EQUIPMENT_STATUS_FIELDS if kind is ExtractKind.EQUIPMENT_STATUS else RELEASE_NOTE_FIELDS
    components = _components(sheet) if kind is ExtractKind.EQUIPMENT_STATUS else {}
    locations = record_locations(sheet) if kind is ExtractKind.EQUIPMENT_STATUS else ()
    covered = set()
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
        if kind is ExtractKind.EQUIPMENT_STATUS:
            sources, location, location_issues = restore_required_sources(values, sources, locations, number)
            row_issues.extend(location_issues)
            if location is not None:
                covered.add(id(location))
        normalized, key, notes = normalize(values, kind, number, sources)
        row_issues.extend(notes)
        rows.append(ReviewRow(normalized, raw_values, sources, key, tuple(row_issues)))
        issues.extend(row_issues)
    if not rows:
        issues.append(ExtractionIssue("AI 응답에 추출된 행이 없습니다. 원본 양식과 응답을 확인하세요."))
    if kind is ExtractKind.EQUIPMENT_STATUS:
        issues.extend(hpa_coverage_issues(sheet, locations, covered, rows))
        issues.extend(equipment_coverage_issues(sheet, rows, locations))
    review = _review(sheet, kind, tuple(rows), tuple(issues), originals)
    return replace(response, data=review, message="추출 결과를 원본과 대조했습니다. 검토 후 검증을 진행하세요.")


def recheck_hpa_coverage(sheet: SheetData, rows) -> tuple[ExtractionIssue, ...]:
    """사용자가 확정한 현재 원본 주소로 HPA 레코드 누락만 다시 검사한다.

    param
    - sheet: 최초에 읽은 원본 스냅샷. 현재값으로 원본을 덮어쓰지 않는다.
    - rows: (현재 필드값 dict, 필드별 원본 주소 dict) 순서쌍 목록. PM은 현재값으로 구분한다.

    return
    - 아직 확인되지 않은 HPA 위치의 global 차단 사유. 행별 필수값 검증은 별도로 수행한다.
    """
    sheet = deepcopy(sheet)
    originals = {sheet.address_of(i, j): value for i, row in enumerate(sheet.values) for j, value in enumerate(row)}
    locations = record_locations(sheet)
    covered, reviews = set(), []
    for values, addresses in rows:
        sources = {field: source for field, address in addresses.items()
                   if (source := _source(sheet, originals, address)) is not None}
        location = match_location(sources, locations, values)
        if location is not None:
            covered.add(id(location))
        reviews.append(ReviewRow(dict(values), dict(values), sources, None))
    return (*hpa_coverage_issues(sheet, locations, covered, reviews),
            *equipment_coverage_issues(sheet, reviews, locations))


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
