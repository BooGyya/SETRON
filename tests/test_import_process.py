"""설비 현황표의 '공정' 열 읽기 (11/11 멘토링: 같은 설비여도 쓰는 공정이 달라 공정별 버전 분포 · 파편화를 보고 싶다).

- 규칙 추출: 블록마다 있는 '공정' · '공정명' 열을 그 블록 행의 공정으로 읽고 근거 칸을 남긴다. 비어 있으면 비운다(선택값).
- 검토 표: 'PM' 다음에 '공정', 앱이 설비군 표기에서 정하는 Inline/Cluster(예전 '구조 구분')는 맨 뒤.
- DB 저장(설비 단위 / PM 단위)은 멘토 확인 후 다음 단계에서 정한다.
"""

import pytest

from setron.features.ai.schemas import EQUIPMENT_STATUS_FIELDS, ExtractKind
from setron.features.ai.service import EXTRACT_RULES
from setron.features.excel_import.schemas import FIELD_LABELS
from setron.features.rule_extraction.service import rule_extract
from setron.infrastructure.excel_reader import SheetData

STATUS = ExtractKind.EQUIPMENT_STATUS


def status_sheet(header, rows):
    """평택 현황표 모양: A1 블록 제목, 2행 머리글(No · Bay · 설비군 · 설비호기 · PM · Version · <header>)."""
    values = [["V2\n( 2F )", "Poly", None, None, None, None, None, None],
              [None, "No", "Bay", "설비군", "설비호기", "PM", "Version", header]]
    for index, (code, process) in enumerate(rows, 1):
        values.append([None, index, None, "M-C4", code, None, "v4.57.2.2_f1", process])
    return SheetData("평택.xlsx", "V2", 1, 1, values)


@pytest.mark.parametrize("header", ["공정", "공정명", "Process", "공정(Process)"])
def test_process_column_is_read_with_its_cell(header):
    result = rule_extract(STATUS, status_sheet(header, [("ESPGH35", "ETCH"), ("ESPGQ93", None)]))
    rows = result.data.rows
    assert [row.values.get("process_name") for row in rows] == ["ETCH", None]
    assert rows[0].sources["process_name"] == "H3" and "process_name" not in rows[1].sources


def test_sheets_without_a_process_column_still_read():
    values = [["V2\n( 2F )", "Poly", None, None, None, None, None],
              [None, "No", "Bay", "설비군", "설비호기", "PM", "Version"],
              [None, 1, None, "M-C4", "ESPGH35", None, "v4.57.2.2_f1"]]
    rows = rule_extract(STATUS, SheetData("평택.xlsx", "V2", 1, 1, values)).data.rows
    assert rows[0].values.get("process_name") is None and rows[0].values["equipment_code"] == "ESPGH35"


def test_review_columns_show_process_and_move_inline_cluster_to_the_end():
    fields = EQUIPMENT_STATUS_FIELDS
    assert fields.index("process_name") == fields.index("pm_name") + 1 and fields[-1] == "structure_type"
    assert FIELD_LABELS["process_name"] == "공정" and FIELD_LABELS["structure_type"] == "Inline/Cluster"
    assert "process_name" in EXTRACT_RULES
