"""가져오기 화면의 원본 스냅샷과 실제 AI 추출 서비스를 연결한다."""

from setron.features.excel_import.service import extract_cells
from setron.features.extraction.schemas import ExtractionReview
from setron.features.extraction.service import extract_sheet_data, review_extraction_result
from setron.infrastructure.ai_client import OpenAICompatibleChatClient
from setron.features.excel_import.source_sheet import sheet_from_snapshot
from setron.features.rule_extraction import rule_extract


class LiveSheetExtractor:
    """환경변수로 설정한 실제 AI 추출 공급자."""

    def __init__(self, client_factory=None):
        """호출 시점에만 생성할 클라이언트 공급자를 보관한다.

        param
        - client_factory: 시험용 호출 공급자. None이면 SETRON_AI_* 설정 사용.
        """
        self.client_factory = client_factory or OpenAICompatibleChatClient.from_env

    def __call__(self, kind, sheet):
        """워커에서 실제 AI를 호출하고 원본 대조를 마친 검토 결과를 반환한다.

        param
        - kind, sheet: 추출 종류와 사용자가 읽은 원본 SheetData.

        return
        - 원본 대조·HPA 누락 검사를 포함한 AIResult.

        raises
        - AIConfigError: 실제 AI 연결 설정이 없거나 잘못된 경우.
        """
        with self.client_factory() as client:
            return extract_sheet_data(client, kind, sheet)


def rule_extractor(kind, sheet):
    """정해진 양식(설비 현황표·세로형 릴리즈노트)을 규칙으로 읽는다. 아는 양식이 아니면 None (→ AI 추출).

    화면 셸(app.py)이 업무 모듈을 직접 불러오지 않도록 가져오기 연결 모듈에서 내보낸다.
    """
    return rule_extract(kind, sheet)


def extract_for_review(kind, workbook_name, snapshot, *, extractor=None, sheet_extractor=None, rules=None):
    """신규·기존 추출 공급자를 동일한 원본 검증 경계로 연결한다.

    param
    - kind, workbook_name, snapshot: 추출 종류와 읽은 원본 정보.
    - extractor: 기존 (종류, 시트명, 셀 목록) 공급자. 결과도 원본 검증을 수행.
    - sheet_extractor: SheetData를 받는 실제 추출 공급자.
    - rules: (종류, SheetData) → 규칙 추출 결과 또는 None. 주면 먼저 시도하고, 정해진 양식이 아니거나
      원본 대조를 통과하지 못하면 AI 추출로 넘긴다 (팀 결정 2026-11-05: 규칙 우선, 처음 보는 양식만 AI).

    return
    - 원본 대조와 누락 검사를 거친 AIResult.
    """
    sheet = sheet_from_snapshot(workbook_name, snapshot)
    if rules is not None:
        ruled = rules(kind, sheet)
        if ruled is not None:
            review = review_extraction_result(kind, sheet, ruled)
            if review.ok:
                return review
    if extractor is not None:
        result = extractor(kind, snapshot.sheet_name, extract_cells(snapshot))
        return review_extraction_result(kind, sheet, result)
    result = (sheet_extractor or LiveSheetExtractor())(kind, sheet)
    if result.ok and not isinstance(result.data, ExtractionReview):
        return review_extraction_result(kind, sheet, result)
    return result
