"""설비 현황 원본의 호기 코드를 우선 사용하고 반복 선정분만 같은 명명 패턴으로 늘린다.

코드 접두사로 모델을 추정하지 않는다. 선택한 template의 모델·계열·관측 이력은 그대로
두며, 원본 전체에 존재하는 호기를 예약해 새 코드와 충돌하지 않도록 한다.
"""

from collections import defaultdict
import re

_CODE = re.compile(r"([A-Za-z][A-Za-z0-9-]*?)(\d+)")
_RAW_CODE = re.compile(r"(ES[A-Z0-9-]*\d)(?:\s*\([^)]*\))?")


def _reserved_codes(catalog):
    reserved = {template["source_equipment"] for template in catalog["templates"]}
    # 제외된 현황 행, 선정하지 않은 설비, '(JEP)' 같은 꼬리말이 있는 원본 호기도 예약한다.
    for book in catalog["workbooks"]:
        if book.get("synthetic"):
            continue
        for cells in book["sheets"].values():
            for value in cells.values():
                if isinstance(value, str) and (match := _RAW_CODE.fullmatch(value.strip())):
                    reserved.add(match[1])
    return reserved


def original_equipment_conflicts(catalog):
    """같은 호기가 여러 원본 위치에 있는 경우 위치·모델·라인·버전 차이를 별도로 반환한다."""
    occurrences = defaultdict(list)
    for template in catalog["templates"]:
        location = {"workbook": template["workbook"],
                    "workbook_name": catalog["workbooks"][template["workbook"]]["name"],
                    "sheet": template["sheet"], "equipment_cell": template["equipment_cell"],
                    "site": template["site"], "line": template["line"], "model": template["model"],
                    "family": template["family"], "version": template["version"]}
        if location not in occurrences[template["source_equipment"]]:
            occurrences[template["source_equipment"]].append(location)
    return [{"equipment_code": code, "issue": "DUPLICATE_ORIGINAL_EQUIPMENT_CODE",
             "different_fields": [field for field in ("site", "line", "model", "family", "version")
                                  if len({location[field] for location in locations}) > 1],
             "locations": locations,
             "resolution": "원본을 합치거나 별도 실제 설비라고 단정하지 않음. 선정 시 같은 코드는 한 번만 원문으로 사용"}
            for code, locations in sorted(occurrences.items()) if len(locations) > 1]


def _next_code(source, reserved, used):
    match = _CODE.fullmatch(source)
    if match is None:
        raise ValueError(f"{source}: 원본의 문자 접두사·숫자 접미사 패턴을 확인할 수 없습니다.")
    prefix, suffix = match.groups()
    number, width = int(suffix), len(suffix)
    maximum = 10 ** width
    for start, stop in ((number + 1, maximum), (1, number + 1)):
        for candidate_number in range(start, stop):
            candidate = prefix + str(candidate_number).zfill(width)
            if candidate not in reserved and candidate not in used:
                return candidate
    raise ValueError(f"{source}: 원본의 숫자 자릿수를 유지하며 추가할 수 있는 호기 코드가 없습니다.")


def assign_equipment_codes(catalog, plan):
    """원본 우선 호기를 plan에 반영하고, 각 호기의 원본·패턴 증강 근거 목록을 반환한다.

    라인 누락을 보완한 template 선정 이후 호출한다. 모든 이름을 검증한 뒤 code만 바꾸므로
    실패한 호출이 부분 변경을 남기지 않는다. 원본 catalog·template·관측은 수정하지 않는다.
    """
    reserved = _reserved_codes(catalog)
    conflicts = {entry["equipment_code"]: entry for entry in original_equipment_conflicts(catalog)}
    used, assignments = set(), []
    for position, entry in enumerate(plan, 1):
        template = catalog["templates"][entry["template"]]
        source = template["source_equipment"]
        if not isinstance(source, str) or not source:
            raise ValueError("원본 설비 호기 코드가 비어 있습니다.")
        repeated = source in used
        code = _next_code(source, reserved, used) if repeated else source
        used.add(code)
        match = _CODE.fullmatch(source)
        pattern = {"source_code": source, "source_workbook": catalog["workbooks"][template["workbook"]]["name"],
                   "source_sheet": template["sheet"], "source_cell": template["equipment_cell"],
                   "rule": "원본 코드 그대로 사용"}
        if repeated:
            pattern.update(prefix=match[1], numeric_width=len(match[2]), source_suffix=match[2],
                           rule="같은 원본 접두사와 숫자 자릿수 유지. 원본 전체와 앞서 배정한 코드에 없는 다음 번호 사용")
        assignments.append({"plan_index": position, "equipment_code": code, "source_equipment": source,
                            "source_kind": "PATTERN_AUGMENTED" if repeated else "ORIGINAL_EXCEL",
                            "workbook": template["workbook"], "sheet": template["sheet"],
                            "equipment_cell": template["equipment_cell"], "model": template["model"],
                            "family": template["family"], "source_site": template["site"],
                            "source_line": template["line"], "pattern_basis": pattern,
                            "original_code_conflict": conflicts.get(source)})
    if len(used) != len(plan):
        raise ValueError("전체 사업장에서 고유한 설비 호기 코드를 배정하지 못했습니다.")
    for entry, assignment in zip(plan, assignments, strict=True):
        entry["code"] = assignment["equipment_code"]
    return assignments
