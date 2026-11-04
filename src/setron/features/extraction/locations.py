"""확정 머리글과 원본 출처로 HPA 레코드의 누락·빈 필수값 위치를 확인한다."""

from copy import deepcopy
from dataclasses import dataclass
import re

from setron.infrastructure.excel_reader import SheetData, cell_address

from .schemas import CellSource, ExtractionIssue


_ALIASES = {
    "base_date": ("base_date", "기준일", "기준날짜"),
    "site": ("site", "사업장"),
    "group_name": ("group_name", "설비군", "분류"),
    "model_name": ("model_name", "model", "모델", "모델명", "설비모델"),
    "family_name": ("family_name", "SW계열", "버전계열"),
    "equipment_code": ("equipment_code", "호기", "설비호기", "설비코드", "장비호기"),
    "version_raw": ("version_raw", "version", "sw", "swver", "swversion", "SW버전", "SW버전원문"),
    "pm_name": ("pm_name", "PM", "PM명", "PM위치"),
}
_CLASSIFIERS = frozenset(("group_name", "model_name", "family_name"))
_IDENTITY = frozenset(("equipment_code", "version_raw", "pm_name"))
_REQUIRED = ("base_date", "site", "model_name", "equipment_code", "family_name", "version_raw")


def _token(value):
    """머리글 비교에만 공백·구분 기호·대소문자 차이를 없앤다.

    param
    - value: 원본 머리글 후보.

    return
    - 비교용 문자열. 문자열이 아닌 원본은 빈 문자열.
    """
    return re.sub(r"[\s_./-]+", "", value).casefold() if isinstance(value, str) else ""


_HEADERS = {_token(alias): field for field, aliases in _ALIASES.items() for alias in aliases}


def equipment_header_rows(sheet):
    """호기·SW 버전 열이 함께 확인된 세로형 표의 머리글 행만 반환한다."""
    return tuple(sheet.first_row + index for index, row in enumerate(sheet.values)
                 if {"equipment_code", "version_raw"} <= {_HEADERS.get(_token(value)) for value in row})


def equipment_coverage_issues(sheet, rows, locations=None):
    """확정된 세로형 호기·SW 열의 실제 호기 셀과 추출 출처를 대조한다. 머리글과 병합 제목은 제외한다."""
    grid, mappings, expected = _Grid(sheet), [], set()
    for row in range(sheet.first_row, grid.last_row + 1):
        headers = [(col, _HEADERS[_token(cell.value)]) for col in range(sheet.first_col, grid.last_col + 1)
                   if (cell := grid.source(row, col)) and _token(cell.value) in _HEADERS]
        current = [mapping for mapping in _segments(headers)
                   if {"equipment_code", "version_raw"} <= mapping.keys()
                   and mapping["equipment_code"] is not None and mapping["version_raw"] is not None]
        if current:
            mappings = current
            continue
        for mapping in mappings:
            equipment = grid.source(row, mapping["equipment_code"])
            version = grid.source(row, mapping["version_raw"])
            if (equipment and version and equipment.address != version.address
                    and equipment.value is not None and str(equipment.value).strip()
                    and _token(equipment.value) not in _HEADERS):
                expected.add(equipment.address)
    # HPA는 PM별 일치와 빈 필수값까지 확인하는 기존 검사에서 더 구체적으로 보고한다.
    locations = record_locations(sheet) if locations is None else locations
    expected.difference_update(cell.address for location in locations if location.is_hpa
                               and (cell := location.fields.get("equipment_code")))
    covered = {source.address for row in rows if (source := row.sources.get("equipment_code"))}
    return tuple(ExtractionIssue(
        "이 호기의 추출 결과가 없습니다. 원본을 확인하고 해당 행을 보완하거나 다시 추출해 주세요.",
        "equipment_code", address, code="EQUIPMENT_COVERAGE_MISSING", blocks_save=True,
    ) for address in sorted(expected - covered, key=lambda address: (
        int(re.search(r"[0-9]+$", address)[0]), address)))


def _is_hpa(value):
    """설명문이나 부분 문자열을 분류로 오인하지 않고 원본 HPA 표기만 확인한다.

    param
    - value: 원본 셀 값.

    return
    - 앞뒤 공백과 대소문자를 제외한 전체 표기가 HPA인지 여부.
    """
    return isinstance(value, str) and value.strip().casefold() == "hpa"


@dataclass(frozen=True)
class RecordLocation:
    """한 데이터 행 또는 열의 실제 셀 위치를 보존한다.

    ivar
    - fields: 유일하게 확인한 필드별 원본 셀. 모호한 머리글의 필드는 포함하지 않는다.
    - hpa_addresses: 해당 레코드의 원본 HPA 식별 셀 또는 구역 제목 주소.
    - pm_name: 명시된 개별 PM 위치. 복수 PM이 같은 셀을 공유해도 각각 구분한다.
    - hpa_context_addresses: 같은 HPA 표의 바로 앞 구역 제목. 다른 분류의 행에는 상속하지 않는다.
    """

    fields: dict[str, CellSource]
    hpa_addresses: frozenset[str]
    pm_name: str | None = None
    hpa_context_addresses: frozenset[str] = frozenset()

    @property
    def is_hpa(self):
        """원본의 모델·분류·계열 또는 명시적 HPA 구역에 속하는지 반환한다.

        return
        - 원본 HPA 식별 근거가 있는 경우 True.
        """
        return bool(self.hpa_addresses)


class _Grid:
    """실제로 읽은 좌표와 병합된 표시값을 함께 제공한다. 원본은 변경하지 않는다."""

    def __init__(self, sheet):
        """원본의 실제 좌표를 보관한다.

        param
        - sheet: 읽은 시점의 셀과 병합 정보를 가진 시트.
        """
        self.sheet = sheet
        self.original = {(sheet.first_row + i, sheet.first_col + j): value
                         for i, row in enumerate(sheet.values) for j, value in enumerate(row)}
        self.last_row = max((r for r, _ in self.original), default=sheet.first_row)
        self.last_col = max((c for _, c in self.original), default=sheet.first_col)

    def source(self, row, col):
        """읽은 좌표를 실제 원본 셀 또는 병합 앵커로 변환한다.

        param
        - row, col: Excel의 1부터 시작하는 원본 행·열 번호.

        return
        - 원본 값의 복사본을 포함한 출처. 실제로 읽지 않은 좌표는 None.
        """
        if (row, col) not in self.original:
            return None
        for merged in self.sheet.merged_ranges:
            if merged.contains(row, col):
                row, col = merged.top, merged.left
                break
        if (row, col) not in self.original:
            return None
        return CellSource(self.sheet.sheet_name, cell_address(row, col), deepcopy(self.original[row, col]))


def _segments(headers):
    """반복 머리글을 경계로 병렬 표를 나누고 중복 열은 미확인으로 둔다.

    param
    - headers: 원본 축 순서의 (위치, 필드 이름) 목록.

    return
    - 표별 필드 위치를 차례로 제공하는 반복자. 모호한 항목의 위치는 None.
    """
    groups, current = [], {}
    for position, field in headers:
        if field in current:
            groups.append(current)
            current = {}
        current[field] = position
    if current:
        groups.append(current)
    combined = []
    for group in groups:
        if combined and not all({"equipment_code", "version_raw"} <= part.keys() for part in (combined[-1], group)):
            previous = combined.pop()
            overlap = previous.keys() & group.keys()
            combined.append({field: None if field in overlap else axis
                             for field, axis in {**previous, **group}.items()})
        else:
            combined.append(group)
    yield from combined


def _pm_names(value):
    """PM 머리글 아래에 명시된 단일 위치·쉼표 목록만 분리한다.

    param
    - value: PM 위치 원문. 설명문에 섞인 위치는 해석하지 않는다.

    return
    - 명시된 PM 위치의 중복 없는 튜플. 확정 형식이 아니면 빈 튜플.
    """
    pattern = r"PM\s*[1-9][0-9]*(?:\s*[,/]\s*(?:PM\s*)?[1-9][0-9]*)*"
    if not isinstance(value, str) or not re.fullmatch(pattern, value.strip(), re.IGNORECASE):
        return ()
    return tuple(dict.fromkeys("PM" + number for number in re.findall(r"[0-9]+", value)))


def record_locations(sheet: SheetData) -> tuple[RecordLocation, ...]:
    """호기·SW 머리글이 있는 가로·세로 표의 실제 데이터 위치를 찾는다.

    HPA를 값으로 만드는 함수가 아니다. 모델·분류 셀의 원문 또는 바로 앞의 명시적
    HPA 구역 제목만 사용하며, 병합된 모델 아래의 여러 호기·PM은 각각 유지한다.

    param
    - sheet: 읽은 시점의 원본 셀과 병합 정보를 가진 시트.

    return
    - 확정 머리글 아래의 레코드 위치 튜플. 위치가 모호한 필드는 제외한다.
    """
    grid = _Grid(sheet)
    found = {}
    for transposed in (False, True):
        first_head, last_head = ((sheet.first_col, grid.last_col) if transposed
                                 else (sheet.first_row, grid.last_row))
        first_axis, last_axis = ((sheet.first_row, grid.last_row) if transposed
                                 else (sheet.first_col, grid.last_col))

        def source(head, axis):
            """표의 방향에 맞춰 축 좌표의 원본을 조회한다.

            param
            - head, axis: 현재 방향의 데이터 축과 필드 축 좌표.

            return
            - 실제로 읽은 원본 셀. 범위 밖이면 None.
            """
            return grid.source(axis, head) if transposed else grid.source(head, axis)

        for head in range(first_head, last_head + 1):
            headers = [(axis, _HEADERS[_token(cell.value)]) for axis in range(first_axis, last_axis + 1)
                       if (cell := source(head, axis)) and _token(cell.value) in _HEADERS]
            for mapping in _segments(headers):
                if not {"equipment_code", "version_raw"} <= mapping.keys():
                    continue
                axes = [axis for axis in mapping.values() if axis is not None]
                if not axes:
                    continue
                lower, upper = min(axes), max(axes)
                section = frozenset()
                for previous in range(head - 1, first_head - 1, -1):
                    title = [cell for axis in range(lower, upper + 1)
                             if (cell := source(previous, axis)) and cell.value is not None and str(cell.value).strip()]
                    if not title:
                        continue
                    # 제목과 머리글 사이의 공란만 건너뛴다. 앞 표의 데이터 행은 제목이 아니다.
                    if all(_is_hpa(cell.value) for cell in title):
                        section = frozenset(cell.address for cell in title)
                    break
                if not _CLASSIFIERS.intersection(mapping) and not section:
                    continue
                separated = False
                for record in range(head + 1, last_head + 1):
                    fields = {field: cell for field, axis in mapping.items()
                              if axis is not None and (cell := source(record, axis))}
                    if not any(cell.value is not None and str(cell.value).strip() for cell in fields.values()):
                        # 같은 머리글·구역의 빈 구분 행·열은 데이터 누락의 근거가 아니다.
                        separated = True
                        continue
                    # 새 머리글을 데이터로 소비하거나 다음 표까지 같은 구역으로 확장하지 않는다.
                    header_fields = {_HEADERS.get(_token(cell.value)) for cell in fields.values()}
                    if {"equipment_code", "version_raw"} <= header_fields:
                        break
                    markers = frozenset(cell.address for field, cell in fields.items()
                                        if field in _CLASSIFIERS and _is_hpa(cell.value))
                    if separated and any(isinstance(cell.value, str) and re.match(
                            r"(?:범례|주석|참고)(?:\s*[:：]|\s|$)|분석\s*제외", cell.value.strip())
                            for cell in fields.values()):
                        section = frozenset()
                        continue
                    # 전체 병합 구역 제목에서 이전 분류 상속을 끝낸다. 제목 자체는 설비가 아니다.
                    visible = [cell for cell in fields.values() if cell.value is not None and str(cell.value).strip()]
                    if len(visible) > 1 and len({cell.address for cell in visible}) == 1 \
                            and all(field in fields and fields[field].address == visible[0].address
                                    for field in ("equipment_code", "version_raw")):
                        section = frozenset({visible[0].address}) if _is_hpa(visible[0].value) else frozenset()
                        separated = True
                        continue
                    if separated and not markers and not (section and not _CLASSIFIERS.intersection(mapping)):
                        continue
                    if not _CLASSIFIERS.intersection(mapping):
                        markers = section
                    # 병합 분류만 이어진 완전 공란은 별도 설비라고 단정하지 않는다.
                    own_marker = any(cell.address == (cell_address(axis, record) if transposed else cell_address(record, axis))
                                     for field, axis in mapping.items() if field in _CLASSIFIERS
                                     and (cell := fields.get(field)) and _is_hpa(cell.value))
                    own_metadata = any(cell.address == (cell_address(axis, record) if transposed else cell_address(record, axis))
                                       for field, axis in mapping.items() if field in {"base_date", "site"}
                                       and (cell := fields.get(field)) and cell.value is not None and str(cell.value).strip())
                    if markers and not own_marker and not any(fields[field].value is not None
                            and str(fields[field].value).strip() for field in _IDENTITY if field in fields) and not own_metadata:
                        continue
                    names = _pm_names(fields["pm_name"].value) if "pm_name" in fields else ()
                    for pm in names or (None,):
                        key = tuple((field, cell.address) for field, cell in sorted(fields.items())), pm
                        found.setdefault(key, RecordLocation(fields, markers, pm, section if markers else frozenset()))
    return tuple(found.values())


def _classifiers_match(sources, location, values):
    """분류 출처와 현재값이 같은 원본 레코드의 HPA 사실을 보존하는지 확인한다.

    param
    - sources, values: 원본 출처와 추출 또는 사용자가 명시적으로 보완한 현재값.
    - location: 비교할 원본 데이터 위치.

    return
    - 모델·설비군·계열 출처가 같은 위치에 속하고 HPA를 일반 분류로 바꾸지 않았으면 True.
    """
    hpa_sources = location.hpa_addresses | location.hpa_context_addresses
    if location.is_hpa and any(values.get(field) and not _is_hpa(values[field])
                               for field in ("model_name", "group_name")):
        return False
    for field in _CLASSIFIERS:
        source, expected, value = sources.get(field), location.fields.get(field), values.get(field)
        if expected is not None and _is_hpa(expected.value) and value and not _is_hpa(value):
            return False
        if source is None:
            continue
        if expected is not None and source.address == expected.address:
            continue
        # S5처럼 분류 HPA 한 셀이 모델·계열을 함께 나타내는 경우만 같은 표의 근거를 공유한다.
        if location.is_hpa and source.address in hpa_sources and _is_hpa(source.value) and (not value or _is_hpa(value)):
            continue
        return False
    return True


def match_location(sources, locations, values):
    """서로 충돌하지 않는 필드별 출처가 유일하게 가리키는 데이터 위치만 반환한다.

    param
    - sources: 원본과 대조한 현재 필드별 출처.
    - locations: 확정 머리글에서 찾은 전체 레코드 위치.
    - values: PM 위치 구분에 사용할 현재 추출값. 원본에 없는 값을 생성하지 않는다.

    return
    - 유일하게 대응하는 위치. 대응이 없거나 여러 개면 None.
    """
    candidates = []
    for location in locations:
        if not _classifiers_match(sources, location, values):
            continue
        if location.pm_name is not None:
            pm = re.fullmatch(r"(?:PM\s*)?([1-9][0-9]*)", (values.get("pm_name") or "").strip(), re.IGNORECASE)
            if pm is None or "PM" + pm[1] != location.pm_name:
                continue
        if any(field in sources and field in location.fields
               and sources[field].address != location.fields[field].address for field in _IDENTITY):
            continue
        evidence = any(field in location.fields and source.address == location.fields[field].address
                       for field, source in sources.items())
        evidence = evidence or any(source.address in (location.hpa_addresses | location.hpa_context_addresses)
                                   for field, source in sources.items() if field in _CLASSIFIERS)
        if evidence:
            candidates.append(location)
    if len(candidates) > 1:
        # 공유 구역 제목만으로 모호한 경우, 실제 데이터 칸에 기록된 날짜·사업장
        # 출처가 가리키는 행·열로만 좁힌다. 표 밖의 공통 메타데이터는 근거로 쓰지 않는다.
        metadata = {field: sources[field].address for field in ("base_date", "site") if field in sources
                    and any(field in location.fields and sources[field].address == location.fields[field].address
                            for location in candidates)}
        candidates = [location for location in candidates
                      if all(field in location.fields and location.fields[field].address == address
                             for field, address in metadata.items())]
    return candidates[0] if len(candidates) == 1 else None


def restore_required_sources(values, sources, locations, number):
    """확정 머리글·레코드 교차점의 출처만 보완한다. 필드 값은 자동으로 채우지 않는다.

    param
    - values, sources: 현재 추출값과 원본 대조를 마친 출처.
    - locations: 확정 머리글에서 찾은 전체 레코드 위치.
    - number: Excel 행 번호와 구분하는 1부터 시작하는 추출 행 번호.

    return
    - 출처 복사본, 유일하게 대응한 레코드 위치, 호기·SW의 위치 미확인 차단 사유.
    """
    sources = dict(sources)
    location = match_location(sources, locations, values)
    issues = []
    for field in _REQUIRED:
        if (values.get(field) or "").strip() or field in sources:
            continue
        if location is not None and field in location.fields:
            sources[field] = location.fields[field]
        elif field in {"equipment_code", "version_raw"}:
            issues.append(ExtractionIssue("필수값의 원본 위치를 확인하지 못했습니다. 원본 셀 위치를 지정하고 값을 보완하세요.",
                                          field, None, number, code="REQUIRED_SOURCE_UNRESOLVED", blocks_save=True))
    return sources, location, tuple(issues)


def hpa_coverage_issues(sheet, locations, covered, rows):
    """HPA 데이터 위치의 누락을 응답 수가 아닌 검증된 원본 출처로 검사한다.

    param
    - sheet: 변경하지 않고 보존한 원본 시트.
    - locations: 확정 머리글에서 찾은 전체 레코드 위치.
    - covered: 응답과 유일하게 연결된 위치 객체의 식별자 집합.
    - rows: 원본과 대조한 추출 행 목록. 비정형 HPA 출처 확인에 사용한다.

    return
    - HPA 레코드 누락과 비정형 위치 미확인의 전체 묶음 차단 사유 튜플.
    """
    issues = []
    known_markers = set()
    for location in locations:
        known_markers.update(location.hpa_addresses)
        known_markers.update(location.hpa_context_addresses)
        if location.is_hpa and id(location) not in covered:
            address = sorted(location.hpa_addresses)[0]
            labels = {"equipment_code": "호기", "version_raw": "SW 버전", "pm_name": "PM"}
            positions = ", ".join(f"{labels[field]} {location.fields[field].address}"
                                  for field in ("equipment_code", "version_raw", "pm_name") if field in location.fields)
            if location.pm_name is not None:
                positions += f" ({location.pm_name})"
            issues.append(ExtractionIssue(f"원본 HPA 레코드가 추출 결과에서 확인되지 않습니다 ({positions}). 누락을 보완해 다시 추출하세요.",
                                          "model_name", address, code="HPA_COVERAGE_MISSING", blocks_save=True))
    # 표 구조를 확인할 수 없는 HPA 데이터는 제목·범례와 구분해 명시적으로 검토한다.
    grid = _Grid(sheet)
    used = {source.address for row in rows for field, source in row.sources.items()
            if field in _CLASSIFIERS and _is_hpa(source.value)}
    for (r, c), value in grid.original.items():
        if not _is_hpa(value):
            continue
        address = cell_address(r, c)
        if address in known_markers:
            continue
        row_values = [v for (rr, _), v in grid.original.items() if rr == r and v is not None and str(v).strip()]
        annotation = any(any(word in str(v) for word in ("범례", "주석", "분석 제외", "분석제외", "참고")) for v in row_values)
        data_values = [v for v in row_values if not _is_hpa(v) and _token(v) not in _HEADERS]
        if not annotation and (address in used or len(data_values) >= 2):
            issues.append(ExtractionIssue("HPA 표의 호기·SW 위치를 유일하게 확인하지 못했습니다. 머리글과 원본 위치를 확인해 다시 추출하세요.",
                                          "model_name", address, code="HPA_COVERAGE_UNRESOLVED", blocks_save=True))
    return tuple(issues)
