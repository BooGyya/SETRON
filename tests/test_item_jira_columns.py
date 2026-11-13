"""릴리즈노트 Jira 열 찾기 (11/12 멘토 답변: 설비군마다 릴리즈노트 작성자가 달라 머리글이 제각각이다).

O2 'Issue'(적용대상 아래 두 번째 머리글 줄) · O5 'JIRA'(+ 비고) · O3 'JIRA ID'(+ 비고) · C4 'REMARK'.
A. 머리글 이름(보조 머리글 줄 포함), Jira 열이 비고 열보다 먼저  B. 이름이 없으면 'ETOJ-1234' 모양이 많은 열.
필수값은 버전과 Item 내용뿐이며 Jira가 빈 Item도 저장한다. 어느 열에서 읽었는지 확인 안내를 남긴다.
"""

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.service import build_save_request, draft_from_review, validate
from setron.features.extraction.service import review_extraction_result
from setron.features.rule_extraction.service import rule_extract
from setron.infrastructure.excel_reader import MergedRange, SheetData

TITLE = "P-PJT M-O2 SW. 정기 Release List"


def extract(rows, merged=()):
    sheet = SheetData("노트.xlsx", "Release List", 1, 1, rows, list(merged))
    rule = rule_extract(ExtractKind.RELEASE_NOTE, sheet)
    draft = draft_from_review(review_extraction_result(ExtractKind.RELEASE_NOTE, sheet, rule).data)
    assert not [i for i in validate(draft) if str(i.level).endswith("ERROR")]
    request = build_save_request(draft, "노트")
    validate_save_request_sources(request)
    notes = [n for n in rule.data.review_notes if n.field == "jira_code"]
    return [row.values.get("jira_code") for row in request.rows], notes


def test_o2_issue_column_under_a_two_row_header():
    codes, notes = extract([
        [TITLE] + [None] * 7, [None] * 8,
        ["version", "No", "항목", "우선순위", "Update 내용", "적용대상", None, None],
        [None, None, None, None, None, "Oxide", "Issue", "TC"],
        ["v3.53_1 (2026-02-01)", 1, "PMC", 1, "Gas Mix 검증 추가", "O", "ETOJ-1101", "X"],
        [None, 2, "UI", 2, "Alarm 표시 개선", "O", None, "O"],
        [None, 3, "CTC", 1, "Recipe 저장 확인", "X", "ETOJ-1103", "O"]],
        [MergedRange(3, 6, 3, 8), MergedRange(5, 1, 7, 1)])
    assert codes == ["ETOJ-1101", None, "ETOJ-1103"]
    assert notes[0].address == "G4" and "머리글 'Issue' 열" in notes[0].reason and "3개 중 2개" in notes[0].reason


@pytest.mark.parametrize("headers, first, expected", [
    (["버전", "넘버", "항목", "업데이트내용", "접수라인", "JIRA ID", "비고"],              # O3: JIRA ID만, 비고 코드는 섞지 않음
     ["v4.49.62.77_d10", 1, "PMC", "Heater 보호", "P3", "ETOJ-3301", "ETOJ-9999 참고"], "ETOJ-3301"),
    (["버전명", "No", "비고", "Update내용", "JIRA", "항목", "접수라인"],                     # 비고가 왼쪽이어도 JIRA 열
     ["v3.53_2", 1, "ETOJ-7000 관련", "내용 A", "ETOJ-7001", "UI", "P1"], "ETOJ-7001"),
    (["버전명", "No", "항목", "지라", "Update내용", "접수라인", "비고"],                     # 한글 '지라'
     ["v3.53_5", 1, "UI", "ETOJ-5001", "내용 A", "P1", None], "ETOJ-5001"),
])
def test_jira_column_wins_over_remark_columns(headers, first, expected):
    codes, notes = extract([[TITLE] + [None] * 6, [None] * 7, headers, first])
    assert codes == [expected] and "머리글" in notes[0].reason


def test_unknown_header_falls_back_to_the_column_with_jira_shaped_values():
    codes, notes = extract([
        [TITLE] + [None] * 4, [None] * 5, ["버전명", "No", "항목", "Ref No", "Update내용"],
        ["v3.53_3", 1, "UI", "ETOJ-8001", "내용 A"], [None, 2, "UI", "ETOJ-8002", "내용 B"], [None, 3, "UI", None, "내용 C"]])
    assert codes == ["ETOJ-8001", "ETOJ-8002", None] and "모양 값이 가장 많은 열" in notes[0].reason


def test_a_few_stray_codes_are_not_enough_to_pick_a_column():
    codes, notes = extract([
        [TITLE] + [None] * 4, [None] * 5, ["버전명", "No", "항목", "담당", "Update내용"],
        ["v3.53_6", 1, "UI", "김철수 ETOJ-1", "내용 A"], [None, 2, "UI", "이영희", "내용 B"], [None, 3, "UI", "박민수", "내용 C"]])
    assert codes == [None, None, None] and notes == []


def test_notes_without_jira_still_save_with_version_and_content_only():
    codes, notes = extract([[TITLE] + [None] * 3, [None] * 4, ["버전명", "No", "항목", "Update내용"],
                            ["v3.53_4", None, "UI", "내용 A"]])
    assert codes == [None] and notes == []
