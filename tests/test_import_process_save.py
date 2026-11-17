"""공정 저장 · Inline/Cluster 숨김 · KCTC 표기 변형 (11/13 멘토 답변).

- 공정은 설비 하나에 하나(복합공정은 'AAAA/BBBB' · 'AAAA & BBBB' · '12.9/14.0'처럼 한 칸)이며 적힌 그대로 저장한다.
- SQL 백업은 공정 칸이 있는 새 구조와 없던 예전 구조를 모두 읽고, 공정 값을 잃는 저장은 거부한다.
- 구조(Inline/Cluster)는 설비군 표기에서 앱이 정하는 값이라 검토 표에서 숨기고, KCTC 오류는 모델명 칸에 붙인다.
- 'Inline Ox (CIP)' · 'Ox(Inline)' · '5각 Cluster O2' · 'O2(Cluster)' · 'Cluster Ox(CIP)' 같은 표기도 4개 설비군으로.
"""

import pytest

from setron.features.db_import.dump import (
    DUMP_COLUMNS, LEGACY_SNAPSHOT_COLUMNS, DumpFormatError, allowed_columns, project_row,
)
from setron.features.excel_import.schemas import FIELD_LABELS, HIDDEN_REVIEW_FIELDS
from setron.features.extraction.model_candidates import candidate_changes

FIELDS = ("group_name", "model_name", "family_name", "version_raw", "modification_type", "structure_type")


def snapshot_row(process="__missing__"):
    row = dict.fromkeys(LEGACY_SNAPSHOT_COLUMNS)
    row.update(snapshot_id=1, equipment_id=1, site="화성", model_name="Inline O2", batch_id=1)
    if process != "__missing__":
        row["process_name"] = process
    return row


def test_snapshot_dump_accepts_new_and_older_shapes():
    assert DUMP_COLUMNS["equipment_snapshot"][-1] == "process_name"
    assert allowed_columns("equipment_snapshot") == (DUMP_COLUMNS["equipment_snapshot"], LEGACY_SNAPSHOT_COLUMNS)


@pytest.mark.parametrize("process", ["V8 SDRT", "AAAA/BBBB", "12.9/14.0", "AAAA & BBBB"])
def test_process_is_kept_as_written_when_the_target_has_the_column(process):
    projected = project_row("equipment_snapshot", snapshot_row(process), DUMP_COLUMNS["equipment_snapshot"])
    assert projected["process_name"] == process


def test_older_exports_without_process_fill_it_empty_and_older_targets_cannot_drop_a_value():
    projected = project_row("equipment_snapshot", snapshot_row(), DUMP_COLUMNS["equipment_snapshot"])
    assert projected["process_name"] is None
    assert "process_name" not in project_row("equipment_snapshot", snapshot_row(None), LEGACY_SNAPSHOT_COLUMNS)
    with pytest.raises(DumpFormatError):
        project_row("equipment_snapshot", snapshot_row("V6 CS"), LEGACY_SNAPSHOT_COLUMNS)


def test_inline_cluster_is_hidden_in_the_review_grid():
    assert HIDDEN_REVIEW_FIELDS == {"structure_type"} and FIELD_LABELS["process_name"] == "공정"


@pytest.mark.parametrize("text, model, modification", [
    ("Inline Ox (CIP)", "Inline Ox", "CIP"), ("Ox(Inline)", "Inline Ox", None), ("5각 Cluster O2", "Cluster O2", None),
    ("O2(Cluster)", "Cluster O2", None), ("Cluster Ox(CIP)", "Cluster Ox", "CIP"), ("인라인 O2", "Inline O2", None),
])
def test_kctc_spellings_resolve_to_one_of_the_four_groups(text, model, modification):
    change = candidate_changes({"model_name": text, "version_raw": "v2296.9_k"}, FIELDS, replace_invalid=True)
    assert change["model_name"] == model and change.get("modification_type") == modification


@pytest.mark.parametrize("text", ["M-O2", "Oxide", "Inline", "Inline Ox / Cluster O2"])
def test_non_kctc_or_ambiguous_spellings_are_left_alone(text):
    change = candidate_changes({"model_name": text, "version_raw": "v2296.9_k"}, FIELDS, replace_invalid=True)
    assert change.get("model_name") in (None, text) and change.get("structure_type") is None


def test_kctc_structure_errors_point_at_the_model_name():
    from setron.features.ai.schemas import ExtractKind
    from setron.features.extraction.normalization import normalize

    result = normalize({"model_name": "Inline Ox", "structure_type": "CLUSTER", "group_name": "KCTC"},
                       ExtractKind.EQUIPMENT_STATUS, 3, {})
    issues = result[2]                                   # (값, 기준 키, 확인 항목)
    assert any(issue.field == "model_name" and "Inline Ox · Inline O2 · Cluster Ox · Cluster O2" in issue.reason
               for issue in issues)
