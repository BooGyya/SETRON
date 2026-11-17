"""두 관리 시트를 원본 셀 출처와 함께 읽어 기존 검토·저장 경로로 전달한다."""

from setron.features.ai.schemas import ExtractedRow, ExtractKind, ExtractResult, ReviewNote
from setron.features.extraction.model_candidates import find_candidate
from .status_rules import _text


def parse_management_sheet(kind, sheet):
    if not sheet.values:
        return None
    columns = {_text(value): index for index, value in enumerate(sheet.values[0]) if value is not None}
    equipment = kind is ExtractKind.EQUIPMENT_STATUS
    required = ({"설비 호기", "기준일", "사업장", "모델명(원문)", "SW 버전(원문)", "입력 작업"} if equipment
                else {"관리 모델", "버전(원문)", "Item 번호", "내용", "입력 작업"})
    if not required.issubset(columns):
        return None
    rows, notes = [], []
    for row_index, cells in enumerate(sheet.values[1:], start=1):
        if not any(value is not None and _text(value) != "" for value in cells):
            continue
        values, sources = {}, {}

        def take(field, *headers):
            for header in headers:
                column = columns.get(header)
                if column is not None and column < len(cells) and cells[column] is not None and _text(cells[column]) != "":
                    values[field] = _text(cells[column])
                    sources[field] = sheet.address_of(row_index, column)
                    return

        if equipment:
            for field, header in (("equipment_code", "설비 호기"), ("base_date", "기준일"), ("site", "사업장"),
                                  ("line", "라인"), ("pm_name", "PM"), ("version_raw", "SW 버전(원문)"),
                                  ("structure_type", "구조"), ("geometry_type", "형상"), ("modification_type", "개조 유형"),
                                  ("process_name", "공정")):
                take(field, header)
            take("model_name", *( ("PM 모델", "PM 모델명(원문)", "모델명(원문)") if values.get("pm_name")
                                  else ("모델명(원문)", "대표 모델") ))
            if not values.get("pm_name") and find_candidate(values.get("model_name")) is None:
                take("model_name", "대표 모델", "모델명(원문)")
        else:
            for field, header in (("model_name", "관리 모델"), ("version_raw", "버전(원문)"),
                                  ("release_date", "출시일"), ("item_no", "Item 번호"), ("content", "내용"),
                                  ("structure_type", "구조"), ("modification_mark", "적용 상태"),
                                  ("version_order", "확인된 순서"), ("version_suffix", "접미사"),
                                  ("item_mapping_status", "Item 매핑")):
                take(field, header)
            for field in ("release_date", "version_order"):
                if values.get(field) == "미확인":
                    values.pop(field)
                    sources.pop(field, None)
            take("target_models", "지정 모델", "모델 범위")
            if not values.get("version_raw"):
                notes.append(ReviewNote("버전에 연결되지 않은 Item입니다. 연결할 릴리즈를 확인해 주세요.",
                                        "version_raw", sources.get("content")))
        take("production_area", "생산 영역")
        take("ui_type", "UI")
        take("family_name", "SW 계열", "버전 계열")  # 이전 버전에서 내보낸 파일도 읽는다.
        if not values.get("family_name"):
            candidate = find_candidate(values.get("model_name"))
            if candidate is not None:
                family = candidate.family_name
                for header in ("설비군", "PM 모델", "대표 모델", "관리 모델", "모델명(원문)"):
                    column = columns.get(header)
                    if column is not None and column < len(cells) and family in _text(cells[column]):
                        values["family_name"], sources["family_name"] = family, sheet.address_of(row_index, column)
                        break
        rows.append(ExtractedRow(values, sources))
    return ExtractResult(kind, sheet.sheet_name, tuple(rows), tuple(notes))
