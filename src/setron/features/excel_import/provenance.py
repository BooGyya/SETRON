"""가져오기 저장값을 실제 원본 또는 사용자의 명시적 수정과 다시 대조한다."""

from collections.abc import Mapping
from datetime import date, datetime
import re

from setron.features.ai.schemas import EQUIPMENT_STATUS_FIELDS, RELEASE_NOTE_FIELDS, ExtractKind
from setron.features.extraction.normalization import normalize
from setron.features.extraction.locations import match_location, record_locations
from setron.features.extraction.schemas import CellSource
from setron.features.extraction.service import _matches
from setron.features.storage.validation import StorageValidationError
from .schemas import FIELD_LABELS


_ADDRESS = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})$")
_DATES = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}")
# 출처가 확인된 모델명에서 정규화로 확정되는 항목. family_name 은 'M-C3' → 'C3' 처럼 모델이 계열을 정하는 경우
# (UI 미표기 M-C3는 통합UI, 멘토 확정 2026-10-28). 정규화 결과와 같을 때만 허용하므로 임의 값은 통과하지 않는다.
_DERIVED = {"group_name", "ui_type", "structure_type", "family_name"}
_SPELLINGS = {
    ("ui_type", "LEGACY"): ("구UI", "구 UI", "Legacy", "legacy"),
    ("ui_type", "INTEGRATED"): ("통합UI", "통합 UI", "Integrated", "integrated"),
    ("structure_type", "INLINE"): ("Inline", "inline", "INLINE"),
    ("structure_type", "CLUSTER"): ("Cluster", "cluster", "CLUSTER"),
    ("group_name", "POLY"): ("Poly", "poly"),
    ("group_name", "OXIDE"): ("Oxide", "oxide"),
    ("group_name", "KCTC"): ("kctc",),
}
_CLASSIFIERS = {"model_name", "group_name", "family_name"}
_IDENTIFIERS = {"equipment_code", "version_raw"}


def _text(value):
    """원본의 문자열·날짜·Excel 숫자를 대조 가능한 표기로 바꾼다."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _canonical(address):
    """실제 Excel 단일 셀의 정규 주소를 반환하고 범위·시트명·범위 밖 좌표는 거부한다."""
    match = _ADDRESS.fullmatch(address.strip()) if isinstance(address, str) else None
    if match is None:
        return None
    letters, row = match.groups()
    column = 0
    for letter in letters.upper():
        column = column * 26 + ord(letter) - ord("A") + 1
    if column > 16384 or int(row) > 1048576:
        return None
    return letters.upper() + str(int(row))


def _fail(row, field, message):
    """검토 행·항목과 원본 재검증 실패 사유를 저장 오류로 전달한다."""
    positions = ", ".join(str(value) for value in row.row_ids)
    label = {"edited_fields": "수정 항목", "source_reference": "원본 위치"}.get(field, FIELD_LABELS.get(field, field))
    raise StorageValidationError(f"검토 행 {positions} · {label}: {message}")


def _source_cells(row, field, raw):
    """CSV형과 목록형 출처를 읽고 원본에 실재하는 셀만 반환한다."""
    reference = (row.source_reference or {}).get(field)
    if reference is None:
        return ()
    result = []
    for location in reference if isinstance(reference, list) else (reference,):
        if not isinstance(location, Mapping) or set(location) != {"sheet", "cells"}:
            _fail(row, field, "원본 위치는 시트와 셀로 지정해야 합니다.")
        if location["sheet"] != raw["sheet"]:
            _fail(row, field, "현재 가져온 시트에 없는 원본 위치입니다.")
        addresses = location["cells"]
        if isinstance(addresses, str):
            addresses = [part.strip() for part in addresses.split(",") if part.strip()]
        if not isinstance(addresses, (list, tuple)) or not addresses:
            _fail(row, field, "원본 셀 위치가 필요합니다.")
        for address in addresses:
            canonical = _canonical(address)
            if canonical is None or canonical not in raw["cells"]:
                _fail(row, field, "실제로 읽은 원본 단일 셀 위치를 확인해야 합니다.")
            result.append((canonical, raw["cells"][canonical]))
    if not result:
        _fail(row, field, "원본 셀 위치가 필요합니다.")
    return tuple(result)


def _matches_saved(field, value, original, values, kind, address):
    """BE-007의 원문 분리·확정 정규화만 다시 적용해 저장값의 근거를 확인한다."""
    current, text = _text(value), _text(original)
    if field in {"equipment_code", "version_raw"} and current.strip() == text.strip():
        return current == text
    if current == text:
        return True
    if field == "jira_code":                 # JIRA 열 · 비고 문장 속 'ETOJ-1234': 코드가 모두 그 칸에 적혀 있어야 한다
        from setron.features.item.jira import split_codes

        codes = split_codes(current)
        return bool(codes) and all(code in text for code in codes)
    candidates = [current, text]
    if field in {"base_date", "release_date"}:
        candidates.extend(_DATES.findall(text))
    if field == "pm_name" and re.fullmatch(r"PM[1-9][0-9]*", current):
        candidates.append(current[2:])
    if field == "model_name" and current.startswith("M-O"):
        candidates.append(current[2:])
    # 정규화된 분류·UI·구조값(LEGACY·CLUSTER 등)은 원본 셀의 원래 표기로도 대조한다.
    # 예: 'M-C3 (구UI)' 셀에서 읽은 UI → LEGACY. 원래 표기가 셀에 있고 다시 정규화한 값이 같을 때만 통과한다.
    candidates.extend(_SPELLINGS.get((field, current), ()))
    context = {name: _text(item) if item is not None else None for name, item in values.items()}
    for candidate in dict.fromkeys(candidates):
        if not candidate or not (candidate.strip() == text.strip() or _matches(field, candidate, text, context)):
            continue
        extracted = {**context, field: candidate}
        normalized = normalize(extracted, kind, 1, {field: CellSource("", address, original)})[0].get(field)
        if field == "item_no":
            try:
                if int(_text(normalized).strip()) == value:
                    return True
            except ValueError:
                continue
        elif normalized == value:
            return True
    return False


def _derived(row, field, raw, kind):
    """출처가 확인된 모델·계열이 확정하는 분류·UI·구조만 별도 셀 없이 허용한다.

    확인된 셀의 원문(예: 계열 'C3 구UI' → UI LEGACY)과 원본 대조를 마친 저장값(예: 제목 셀
    'KCTC Inline O2 SW Release List'에서 읽은 모델 'Inline O2' → 구조 INLINE) 두 가지로 다시 정규화해
    하나라도 저장값과 같으면 허용한다. 둘 다 원본에서 확인된 셀에서만 나오므로 임의 값은 그대로 막는다.
    """
    if field not in _DERIVED:
        return False
    texts, saved, cell_sources = {}, {}, {}
    for name in ("model_name", "family_name"):
        value = row.values.get(name)
        if value is None:
            continue
        sources = _source_cells(row, name, raw)
        if name in row.edited_fields:
            texts[name] = saved[name] = value
        elif sources and all(_matches_saved(name, value, original, row.values, kind, address)
                             for address, original in sources):
            address, original = sources[0]
            texts[name] = _text(original) if len(sources) == 1 and original is not None else value
            saved[name] = value
            cell_sources[name] = CellSource("", address, original)
    expected = row.values.get(field)
    return any(normalize(values, kind, 1, cell_sources)[0].get(field) == expected
               for values in (texts, saved) if values)


def _area_matches(row, raw, request):
    """저장할 Memory/Foundry가 확인된 사업장 · 라인의 구분과 같은지. 저장(import_save)과 같은 :func:`area_of`를 쓴다.

    AI가 'Foundry'를 제목 칸 같은 곳을 근거로 넣으면, 칸 글자 전체와 달라 저장 직전에 막혔다(11/12 확인용 V2).
    장소표와 다른 값(예: V2를 Memory)은 통과시키지 않는다.
    """
    from setron.features.reference_data import area_of

    expected = area_of({"site": row.values.get("site"), "line": row.values.get("line")}, raw.get("sheet"),
                       getattr(request, "workbook_name", None))
    return expected is not None and str(row.values.get("production_area") or "").strip().upper() == expected


def _hpa(value):
    """원본의 전체 HPA 표기를 확인하며 제목의 부분 문자열을 분류로 쓰지 않는다."""
    return isinstance(value, str) and value.strip().casefold() == "hpa"


def _record_locations(request):
    """실제 원본을 복원해 HPA가 포함된 표의 레코드 위치만 확인한다."""
    from .schemas import SheetSnapshot
    from .service import parse_address
    from .source_sheet import sheet_from_snapshot

    raw = request.raw_data
    positions = [parse_address(address) for address in raw["cells"]]
    top = min((row for row, _ in positions), default=1)
    left = min((column for _, column in positions), default=1)
    snapshot = SheetSnapshot(raw["sheet"], top, left,
        max((row for row, _ in positions), default=top) - top + 1,
        max((column for _, column in positions), default=left) - left + 1,
        dict(raw["cells"]), tuple(raw.get("merged", ())))
    return record_locations(sheet_from_snapshot(request.workbook_name, snapshot))


def _validate_hpa_classification(row, raw, locations):
    """HPA 식별값과 다른 레코드의 분류 출처를 합친 저장 요청을 거부한다.

    전체 원본의 모든 HPA 행을 저장하도록 강제하지 않는다. 현재 행이 사용하는 원본
    HPA 위치와 분류만 대조하며 정확한 중복이 병합한 출처는 각각 확인한다.
    """
    sources = {field: tuple(CellSource(raw["sheet"], address, original) for address, original in _source_cells(row, field, raw))
               for field in row.values.keys() | (row.source_reference or {}).keys()}
    current_hpa = any(_hpa(row.values.get(field)) for field in _CLASSIFIERS)
    original_hpa = any(_hpa(source.value) for field in _CLASSIFIERS for source in sources.get(field, ()))
    identity_slots = [location for location in locations if location.is_hpa and any(
        field in location.fields and source.address == location.fields[field].address
        for field in _IDENTIFIERS for source in sources.get(field, ()))]
    if not (current_hpa or original_hpa or identity_slots):
        return
    if any(row.values.get(field) and not _hpa(row.values[field]) for field in ("model_name", "group_name")):
        _fail(row, "group_name", "원본 HPA의 호기·SW를 일반 설비 분류와 합칠 수 없습니다.")
    pm = re.fullmatch(r"(?:PM\s*)?([1-9][0-9]*)", _text(row.values.get("pm_name")).strip(), re.IGNORECASE)
    pm_name = "PM" + pm[1] if pm else None
    touched = [location for location in identity_slots if location.pm_name is None or location.pm_name == pm_name]
    if not touched:
        structured_identity = any(field in location.fields and source.address == location.fields[field].address
                                  for location in locations for field in _IDENTIFIERS for source in sources.get(field, ()))
        if not original_hpa or structured_identity:
            _fail(row, "group_name", "HPA 분류와 호기·SW가 같은 원본 레코드에 속하는지 확인해야 합니다.")
        return  # 정형 머리글이 없는 수동 검토도 실제 HPA 분류 출처는 보존한다.
    used = {field: set() for field in (*_CLASSIFIERS, *_IDENTIFIERS, "pm_name")}
    for location in touched:
        selected = {}
        for field, choices in sources.items():
            if not choices:
                continue
            if field in _CLASSIFIERS:
                expected = location.fields.get(field)
                markers = location.hpa_addresses | location.hpa_context_addresses
                matching = [source for source in choices if expected is not None and source.address == expected.address
                            or source.address in markers and _hpa(source.value)]
            elif field in _IDENTIFIERS or field == "pm_name":
                expected = location.fields.get(field)
                matching = [source for source in choices if expected is not None and source.address == expected.address]
            else:
                matching = list(choices)
            if not matching:
                _fail(row, field, "HPA의 분류·호기·SW 출처가 서로 다른 원본 레코드를 가리킵니다.")
            selected[field] = matching[0]
            if field in used:
                used[field].update(source.address for source in matching)
        if match_location(selected, locations, row.values) is not location:
            _fail(row, "group_name", "HPA 분류와 호기·SW·PM의 원본 레코드가 일치하지 않습니다.")
    for field, addresses in used.items():
        if any(source.address not in addresses for source in sources.get(field, ())):
            _fail(row, field, "병합된 HPA 출처에 다른 레코드의 분류·식별값이 섞여 있습니다.")


def validate_save_request_sources(request):
    """DB 접속 전에 저장 요청 전체의 원본 근거와 명시적 수정 항목을 재검증한다.

    param
    - request: 원본 시트·셀, 현재 저장값과 사용자 수정 항목을 가진 SaveRequest.

    raises
    - StorageValidationError: 정규값이라는 이유로 원본 대조를 생략했거나, 확인되지 않은
      원본 값·위치를 사용했거나, 수정 항목 형식이 잘못된 경우.
    """
    raw = request.raw_data
    if not isinstance(raw, Mapping) or not isinstance(raw.get("sheet"), str) or not isinstance(raw.get("cells"), Mapping):
        raise StorageValidationError("검증할 원본 시트와 셀 자료가 필요합니다.")
    kind = ExtractKind(request.kind)
    fields = EQUIPMENT_STATUS_FIELDS if kind is ExtractKind.EQUIPMENT_STATUS else RELEASE_NOTE_FIELDS
    required_sources = {"version_raw"} | ({"equipment_code"} if kind is ExtractKind.EQUIPMENT_STATUS else set())
    locations = _record_locations(request) if kind is ExtractKind.EQUIPMENT_STATUS and any(_hpa(value) for value in raw["cells"].values()) else ()
    for row in request.rows:
        if not isinstance(row.values, Mapping) or row.source_reference is not None and not isinstance(row.source_reference, Mapping):
            _fail(row, "source_reference", "현재값과 원본 위치는 공통 항목별 객체로 전달해야 합니다.")
        if (not isinstance(row.edited_fields, tuple) or any(not isinstance(name, str) or name not in fields for name in row.edited_fields)
                or len(set(row.edited_fields)) != len(row.edited_fields)):
            _fail(row, "edited_fields", "사용자가 명시적으로 수정한 공통 항목 목록이 필요합니다.")
        for name in fields:
            value = row.values.get(name)
            sources = _source_cells(row, name, raw)
            if name in required_sources and not sources:
                _fail(row, name, "호기·SW의 원본 셀 위치는 값을 보완한 뒤에도 확인해야 합니다.")
            if value is None or isinstance(value, str) and not value.strip():
                continue
            if name in row.edited_fields:
                continue
            if name == "production_area" and kind is ExtractKind.EQUIPMENT_STATUS and _area_matches(row, raw, request):
                continue                 # Memory/Foundry는 원본 칸이 아니라 사업장 · 라인으로 정해지는 값이다 (11/12)
            if not sources:
                if _derived(row, name, raw, kind):
                    continue
                _fail(row, name, "AI 값의 원본 셀 위치가 없습니다. 검토에서 확인·수정해야 합니다.")
            if not all(_matches_saved(name, value, original, row.values, kind, address)
                       for address, original in sources):
                _fail(row, name, "원본 셀 값과 다릅니다. 검토에서 값을 확인·수정한 뒤 다시 저장해 주세요.")
        if kind is ExtractKind.EQUIPMENT_STATUS:
            _validate_hpa_classification(row, raw, locations)
