"""저장된 릴리즈노트 원본에서 Item Jira를 읽을 때 가져오기 규칙(release_rules)과 같은 열을 쓴다 (11-13).

11-12 멘토 답변: 설비군마다 릴리즈노트 작성자가 달라 머리글이 다르다(O2 'Issue' · O5 'JIRA' · O3 'JIRA ID' · C4 'REMARK').
Jira가 없는 Item도 있다. 아이템 매니저 · 내보내기 · 빈 Jira 채우기가 이 규칙으로 읽는다.
"""

from setron.features.item.original import (
    JIRA_EMPTY, JIRA_FOUND, JIRA_NO_COLUMN, jira_by_item, original_item_rows_from_batches,
)


def _read(cells, rows, sheet="Release"):
    """한 시트(cells)의 내용 칸 주소(rows)마다 Item 하나를 만들어 Jira를 읽는다."""
    items = [{"item_id": i, "batch_id": 1, "source_reference": {"content": {"sheet": sheet, "cells": [address]}}}
             for i, address in enumerate(rows, 1)]
    found = original_item_rows_from_batches([{"batch_id": 1, "raw_data": {sheet: cells}}], items)
    return jira_by_item(found, [item["item_id"] for item in items])


def test_c3_description_header_still_finds_the_jira_column():
    """C3 노트는 내용 머리글이 'Description'이라 예전에는 머리글 줄을 못 찾아 Jira 열도 못 읽었다."""
    cells = {"A2": "Version", "B2": "No", "C2": "Module", "D2": "Description", "E2": "JIRA", "F2": "담당자",
             "A3": "v3.52_11 (2026-01-05)", "B3": 1, "C3": "Engine", "D3": "Interlock 개선", "E3": "ETOJ-6293",
             "B4": 2, "C4": "PMC", "D4": "Heater 보강", "E4": None}
    jira = _read(cells, ["D3", "D4"])
    assert (jira[1].codes, jira[1].status) == (("ETOJ-6293",), JIRA_FOUND)
    assert jira[2].status == JIRA_EMPTY and jira[2].label == "Jira 없음"   # Jira가 없는 Item도 있다


def test_jira_column_wins_over_remark_and_remark_alone_counts_as_jira():
    # O5: JIRA 열과 비고 열이 둘 다 있으면 JIRA 열만 읽는다 (비고의 코드는 합치지 않는다)
    o5 = {"A3": "버전명", "B3": "No", "C3": "항목", "D3": "JIRA", "E3": "Update내용", "F3": "비고",
          "A4": "v1", "B4": 1, "C4": "UI", "D4": "ETOJ-1", "E4": "화면 수정", "F4": "참고 ETOJ-9"}
    assert _read(o5, ["E4"])[1].codes == ("ETOJ-1",)
    # C4: REMARK가 Jira 열이다. 빈 칸은 '미확인'이 아니라 'Jira 없음'
    c4 = {"A2": "버전명", "B2": "No", "C2": "항목", "D2": "Update내용", "E2": "REMARK",
          "A3": "v4", "B3": 1, "C3": "ALL", "D3": "Interlock", "E3": "ETOJ-4120",
          "B4": 2, "C4": "CTC", "D4": "Recipe", "E4": None}
    jira = _read(c4, ["D3", "D4"])
    assert jira[1].codes == ("ETOJ-4120",) and jira[2].status == JIRA_EMPTY


def test_o2_issue_on_the_second_header_row_and_unnamed_column_by_value_shape():
    # O2: 적용대상(Oxide · Issue · TC) 아래 두 번째 머리글 줄의 Issue가 Jira
    o2 = {"A1": "version", "B1": "No", "C1": "항목", "D1": "Update 내용", "E1": "적용대상",
          "E2": "Oxide", "F2": "Issue", "G2": "TC",
          "A3": "v3.52_10", "B3": 1, "C3": "Engine", "D3": "Trace 저장", "E3": "O", "F3": "ETOJ-3257", "G3": None}
    assert _read(o2, ["D3"])[1].codes == ("ETOJ-3257",)
    # 머리글 이름이 없어도 Item 행 절반 이상에 'ETOJ-1234' 모양이 있는 열에서 읽는다
    unnamed = {"A1": "버전", "B1": "No", "C1": "내용", "D1": "참고",
               "A2": "v1", "B2": 1, "C2": "A 수정", "D2": "ETOJ-11 관련",
               "B3": 2, "C3": "B 수정", "D3": "ETOJ-12"}
    jira = _read(unnamed, ["C2", "C3"])
    assert (jira[1].codes, jira[2].codes) == (("ETOJ-11",), ("ETOJ-12",))


def test_note_without_jira_column_stays_unknown():
    kctc = {"A3": "버전명", "B3": "No", "C3": "항목", "D3": "Update내용",
            "A4": "v1", "B4": 1, "C4": "UI", "D4": "Recipe 확인"}
    assert _read(kctc, ["D4"])[1].status == JIRA_NO_COLUMN
