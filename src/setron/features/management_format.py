"""사용자용 관리 Excel과 다시 가져오기가 공유하는 두 시트의 열 정의."""

from setron.features.extraction.model_candidates import find_candidate

EQUIPMENT_HEADERS = (
    "설비 호기", "기준일", "사업장", "라인", "모델명(원문)", "대표 모델", "생산 영역", "구조", "형상",
    "개조 유형", "PM", "PM 모델", "PM 모델명(원문)", "SW 버전(원문)", "연결 릴리즈", "입력 작업",
    "UI", "설비군",
)
RELEASE_HEADERS = (
    "관리 모델", "버전(원문)", "접미사", "출시일", "확인된 순서", "Item 매핑", "직접 수정 항목", "입력 작업",
    "Item 번호", "내용", "적용 상태", "모델 범위", "지정 모델", "Item 직접 수정 항목", "UI", "구조", "생산 영역", "설비군",
)
PRODUCTION_AREAS = (None, "MEMORY", "FOUNDRY")


def equipment_group_name(model: str) -> str:
    candidate = find_candidate(model)
    if candidate is None:
        return model
    return "DFB" if candidate.model_name == "M-C4 DFB" else candidate.family_name
