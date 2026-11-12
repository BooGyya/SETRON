"""릴리즈노트 Item별 Jira 코드 추출 · 저장 (11/11 멘토링: 버전 · Item · Item별 Jira 코드가 함께 다니는 필수 정보).

- 규칙 추출: JIRA 열(M-O5형)은 값 그대로, 비고 · REMARK(M-C4형)는 'ETOJ-1234' 모양만. 근거 칸을 함께 남긴다.
- 검토 · 저장 요청 · 원본 대조를 통과한다. 빈 Jira는 저장하지 않는다(칸을 아직 못 더한 DB에도 저장).
- 아이템 매니저는 저장된 값을 먼저 쓰고, 없으면 보관 원본 행에서 읽는다.
- 이미 있는 DB: 빈 Jira만 원본 보관에서 한 번 채운다(이미 있는 값은 그대로).
"""

from types import SimpleNamespace

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.excel_import.service import build_save_request, draft_from_review, validate
from setron.features.extraction.service import review_extraction_result
from setron.features.item.jira import codes_in, join_codes, matches_source, normalize_jira, split_codes
from setron.features.item.jira_upgrade import missing_jira
from setron.features.release.schemas import ItemInput
from setron.features.release.validation import prepare_record
from setron.features.rule_extraction.service import rule_extract
from setron.infrastructure.excel_reader import SheetData

RELEASE = ExtractKind.RELEASE_NOTE


def note(header, cells):
    """정기 Release List 모양: 1행 제목, 3행 머리글(버전명 · No · 항목 · <header> · Update내용), 4행부터 Item."""
    values = [["P-PJT M-O5 SW. 정기 Release List", None, None, None, None], [None] * 5,
              ["버전명", "No", "항목", header, "Update내용"]]
    for index, (jira, content) in enumerate(cells, 1):
        values.append(["v4.56.62.1_a1 (2026-01-08)" if index == 1 else None, str(index), "UI", jira, content])
    return SheetData("P_PJT_M-O5_실제.xlsx", "Release List", 1, 1, values)


@pytest.mark.parametrize("header, cell, expected", [
    ("JIRA", "ETOJ-6532", ("ETOJ-6532",)), ("JIRA", "ETOJ-1, ETOJ-2", ("ETOJ-1", "ETOJ-2")), ("JIRA", "-", ()),
    ("REMARK", "고객 요청 ETOJ-4120 반영", ("ETOJ-4120",)), ("REMARK", "고객 요청", ()), ("비고", "ETOJ-9 / ETOJ-10", ("ETOJ-9", "ETOJ-10")),
    ("담당", "ETOJ-1", ()),
])
def test_codes_follow_the_item_manager_rules(header, cell, expected):
    assert codes_in(cell, header) == expected
    assert split_codes(join_codes(expected)) == expected


@pytest.mark.parametrize("header, cells, expected", [
    ("JIRA", [("ETOJ-6532", "MES Recipe 승인 확인"), ("ETOJ-5345", "FCS Step 검증")], ["ETOJ-6532", "ETOJ-5345"]),
    ("REMARK", [("고객 요청 ETOJ-4120", "Recipe Summary 표시"), (None, "Alarm 조건 세분화")], ["ETOJ-4120", None]),
])
def test_rules_extract_jira_with_its_cell_and_it_reaches_the_save_request(header, cells, expected):
    sheet = note(header, cells)
    rule = rule_extract(RELEASE, sheet)
    assert [row.values.get("jira_code") for row in rule.data.rows] == expected
    assert rule.data.rows[0].sources["jira_code"] == "D4"
    draft = draft_from_review(review_extraction_result(RELEASE, sheet, rule).data)
    assert not [i for i in validate(draft) if str(i.level).endswith("ERROR")]
    request = build_save_request(draft, "노트")
    validate_save_request_sources(request)                         # 비고 문장 속 코드도 원본 대조를 통과
    assert [row.values.get("jira_code") for row in request.rows] == expected


def test_empty_jira_is_explicit_none_and_legacy_input_alias_uses_the_canonical_field():
    assert prepare_record(ItemInput(content="내용", item_no=1))["jira_code"] is None
    assert prepare_record(ItemInput(content="내용", item_no=1, jira_code=" ETOJ-1 "))["jira_code"] == "ETOJ-1"
    assert prepare_record(ItemInput(content="내용", item_no=1, jira_code="  "))["jira_code"] is None
    aliased = prepare_record(ItemInput(content="내용", jira_key="ETOJ-1; ETOJ-2"))
    assert aliased["jira_code"] == "ETOJ-1, ETOJ-2" and "jira_key" not in aliased


def test_item_manager_prefers_stored_codes_and_falls_back_to_original_rows():
    from setron.features.item.schemas import Item
    from setron.ui.item_data import ItemManagerData
    from setron.ui.item_original import JIRA_FOUND, OriginalItemRow

    entries = (SimpleNamespace(item=Item(1, 1, "저장된 Item", "ETOJ-1, ETOJ-2")), SimpleNamespace(item=Item(2, 2, "원본만", None)))
    original = (OriginalItemRow(2, 7, "Release List", 5, ("No", "JIRA", "Update내용"), ("2", "ETOJ-9", "원본만")),)
    data = ItemManagerData.__new__(ItemManagerData)
    object.__setattr__(data, "versions", SimpleNamespace(items=entries, original_rows=original))
    jira = data.jira([1, 2])
    assert jira[1].codes == ("ETOJ-1", "ETOJ-2") and jira[1].status == JIRA_FOUND
    assert jira[2].codes == ("ETOJ-9",)


def test_one_time_fill_only_fills_items_without_any_stored_jira_column():
    from setron.ui.item_original import OriginalItemRow

    items = [{"item_id": 1}, {"item_id": 2, "jira_code": "ETOJ-KEEP"}, {"item_id": 3},
             {"item_id": 4, "jira_code": None}, {"item_id": 5, "jira_key": None}]
    original = (OriginalItemRow(1, 7, "Release List", 4, ("No", "JIRA", "Update내용"), ("1", "ETOJ-6532", "가")),
                OriginalItemRow(2, 7, "Release List", 5, ("No", "JIRA", "Update내용"), ("2", "ETOJ-NEW", "나")),
                OriginalItemRow(3, 7, "Release List", 6, ("No", "REMARK", "Update내용"), ("3", "요청 ETOJ-4120", "다")),
                OriginalItemRow(4, 7, "Release List", 7, ("No", "JIRA", "Update내용"), ("4", "ETOJ-7", "라")),
                OriginalItemRow(5, 7, "Release List", 8, ("No", "JIRA", "Update내용"), ("5", "ETOJ-8", "마")))
    assert missing_jira(items, original) == {1: "ETOJ-6532", 3: "ETOJ-4120"}


def test_multiple_codes_are_normalized_without_truncation_or_format_restrictions():
    assert normalize_jira(" etoj-1; ETOJ-2\netoj-1 ") == "etoj-1, ETOJ-2"
    assert normalize_jira("원본 자유 표기") == "원본 자유 표기"
    assert normalize_jira("-; 없음") is None
    assert normalize_jira("X" * 200) == "X" * 200
    with pytest.raises(ValueError, match="200"):
        join_codes(("X" * 200, "Y"))
    with pytest.raises(ValueError, match="문자열"):
        normalize_jira(123)


def test_normalized_codes_must_each_be_in_the_same_original_cell():
    assert matches_source("ETOJ-1, ETOJ-2", "ETOJ-1; ETOJ-2\nETOJ-1")
    assert matches_source("ETOJ-1, ETOJ-2", "고객 요청 ETOJ-1 / ETOJ-2 반영")
    assert not matches_source("ETOJ-1", "ETOJ-100")
    assert not matches_source("ETOJ-1, ETOJ-3", "ETOJ-1; ETOJ-2")


def test_multiple_codes_reach_review_and_source_validation_without_rewriting_the_original():
    sheet = note("JIRA", [("ETOJ-1; ETOJ-2\nETOJ-1", "변경")])
    rule = rule_extract(RELEASE, sheet)
    review = review_extraction_result(RELEASE, sheet, rule)
    assert review.ok
    draft = draft_from_review(review.data)
    request = build_save_request(draft, "복수 Jira")
    validate_save_request_sources(request)
    assert request.rows[0].values["jira_code"] == "ETOJ-1, ETOJ-2"
    assert request.raw_data["cells"]["D4"] == "ETOJ-1; ETOJ-2\nETOJ-1"


def test_overlength_rule_value_is_preserved_and_blocked_in_review():
    original = "X" * 201
    sheet = note("JIRA", [(original, "변경")])
    rule = rule_extract(RELEASE, sheet)
    assert rule.data.rows[0].values["jira_code"] == original
    draft = draft_from_review(review_extraction_result(RELEASE, sheet, rule).data)
    assert any(issue.field == "jira_code" and issue.blocks_save and "200" in issue.message for issue in validate(draft))
