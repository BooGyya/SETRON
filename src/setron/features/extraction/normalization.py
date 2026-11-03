"""출처를 확인한 원문에만 확정된 공통 항목 규칙을 적용한다."""

from datetime import datetime
import re

from setron.features.ai.schemas import ExtractKind
from setron.features.reference_data import MODEL_CATEGORIES as _REFERENCE_CATEGORIES, MODEL_STRUCTURES

from .schemas import ExtractionIssue, FamilyKey


CATEGORIES = {"poly": "POLY", "oxide": "OXIDE", "kctc": "KCTC", "hpa": "HPA"}
UI_TYPES = {"구ui": "LEGACY", "legacy": "LEGACY", "통합ui": "INTEGRATED", "integrated": "INTEGRATED"}
# 등록 모델 14개(멘토 확정 분류표, database/seed_demo_catalog.sql v2)와 계열 이름만 적힌 표기
MODEL_CATEGORIES = {"C3": "POLY", "C4": "POLY", **_REFERENCE_CATEGORIES}


def normalize(values, kind, row_number, sources):
    """원문 복사본을 정리하고 확인이 필요한 해석을 반환한다. 원본은 바꾸지 않는다."""
    result = dict(values)
    issues = []

    def issue(field, reason):
        source = sources.get(field)
        issues.append(ExtractionIssue(reason, field, source.address if source else None, row_number))

    for field, value in result.items():
        if value is not None and field not in {"version_raw", "equipment_code", "engine_version", "epd_version", "content"}:
            result[field] = value.strip() or None

    model = result.get("model_name")
    model_source = sources.get("model_name")
    if model_source and isinstance(model_source.value, str) and re.fullmatch(
            r"M-O[235](?:\s*/\s*O[235])+", model_source.value.strip()):
        # 'M-O2/O3'처럼 원본에서 접두사를 공유한 경우에만 O3를 M-O3로 복원한다.
        members = re.findall(r"O[235]", model_source.value)
        if model in members:
            model = result["model_name"] = "M-" + model
    if model == "M5":
        model = result["model_name"] = "M-O5"
    if model in {"C2", "M-C2"}:
        result["model_name"] = None
        issue("model_name", "C2는 관리 대상이 아닙니다. 원문을 확인하세요.")

    category_raw = result.get("group_name")
    category = CATEGORIES.get((category_raw or "").casefold())
    expected_category = MODEL_CATEGORIES.get(model)
    if category_raw and category is None:
        issue("group_name", "설비군 분류를 Poly·Oxide·KCTC·HPA 중에서 확인하세요.")
    elif category is None:
        category = expected_category
    if category and expected_category and category != expected_category:
        issue("group_name", "설비군 분류와 모델 소속이 다릅니다. 원본을 확인하세요.")
        category = None
    result["group_name"] = category

    pm = result.get("pm_name")
    pm_source = sources.get("pm_name")
    if pm and pm_source and isinstance(pm_source.value, str):
        # 'PM1, 2는 O3, PM3, 4는 O2'의 축약된 2·4를 고정 PM 위치 이름으로 정리한다.
        groups = re.findall(r"\bPM\s*[1-9][0-9]*(?:\s*,\s*[1-9][0-9]*)*", pm_source.value, re.IGNORECASE)
        number = re.fullmatch(r"(?:PM\s*)?([1-9][0-9]*)", pm, re.IGNORECASE)
        if groups and number:
            positions = {position for group in groups for position in re.findall(r"[0-9]+", group)}
            if number[1] in positions:
                result["pm_name"] = "PM" + number[1]
            else:
                result["pm_name"] = None
                issue("pm_name", "추출한 PM 위치가 원본의 PM 목록에 없습니다.")

    area = result.get("production_area")
    if area:
        normalized_area = {"memory": "MEMORY", "foundry": "FOUNDRY"}.get(area.casefold())
        if normalized_area is None:
            issue("production_area", "Memory/Foundry 값을 확인하세요. 모델 소속은 생산 영역으로 추정하지 않습니다.")
        result["production_area"] = normalized_area
    elif kind is ExtractKind.EQUIPMENT_STATUS:
        result["production_area"] = None

    ui = result.get("ui_type")
    normalized_ui = UI_TYPES.get((ui or "").replace(" ", "").casefold())
    if ui and normalized_ui is None:
        issue("ui_type", "구UI 또는 통합UI 구분을 확인하세요.")
    result["ui_type"] = normalized_ui
    family = result.get("family_name")
    is_c3 = model in {"C3", "M-C3"} or family in {"C3", "C3 구UI", "C3 통합UI"}
    if is_c3:
        family_ui = {"C3 구UI": "LEGACY", "C3 통합UI": "INTEGRATED"}.get(family)
        if family_ui and normalized_ui and family_ui != normalized_ui:
            issue("ui_type", "C3 계열 표기와 UI 구분이 상충합니다.")
            normalized_ui = None
        else:
            normalized_ui = normalized_ui or family_ui
        family = "C3"
        if normalized_ui is None and result.get("model_name") in {"C3", "M-C3"}:
            # 멘토 확정(2026-10-28 회신, 4차 멘토링 일지): 구UI/통합UI 표기가 없는 M-C3는 모두 통합UI. 구UI가 특별한 설비다.
            normalized_ui = "INTEGRATED"
        result["ui_type"] = normalized_ui
        if normalized_ui is None:
            issue("ui_type", "C3 구UI·통합UI를 확인하세요.")
    result["family_name"] = family

    structure = result.get("structure_type")
    if structure and structure.casefold() in {"inline", "cluster"}:
        structure = result["structure_type"] = structure.upper()
    model_structure = MODEL_STRUCTURES.get(model)
    if model_structure:
        if structure and structure != model_structure:
            issue("structure_type", "모델과 Inline/Cluster 구조 표기가 상충합니다.")
            structure = None
        else:
            structure = result["structure_type"] = model_structure
    if category == "KCTC" and structure not in {"INLINE", "CLUSTER"}:
        issue("structure_type", "KCTC Inline·Cluster 구분을 확인하세요.")
        structure = None

    for field in ("base_date", "release_date"):
        value = result.get(field)
        if value:
            for pattern in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    result[field] = datetime.strptime(value, pattern).date().isoformat()
                    break
                except ValueError:
                    continue
            else:
                result[field] = None
                issue(field, "날짜의 연·월·일을 확인하세요. 날짜를 임의로 보완하지 않습니다.")

    required = ("base_date", "site", "model_name", "equipment_code", "version_raw") if kind is ExtractKind.EQUIPMENT_STATUS else ("version_raw", "content")
    for field in required:
        if not (result.get(field) or "").strip():
            issue(field, "추출값이 없거나 확인되지 않았습니다. 원본을 확인하고 보완하세요.")
    if result.get("modification_mark") and not result.get("target_models"):
        issue("modification_mark", "개조형 적용 표식의 대상 모델과 의미를 확인하세요.")

    key = None
    if model not in {"C2", "M-C2"} and category and family and (not is_c3 or normalized_ui) and (category != "KCTC" or structure):
        key = FamilyKey(category, family, normalized_ui if is_c3 else None,
                        structure if category == "KCTC" else None, "C3" if is_c3 else result.get("model_name"))
    if key is None:
        issue("family_name", "버전 계열 대응을 확인하세요. 다른 계열과 자동으로 합치지 않습니다.")
    return result, key, tuple(issues)
