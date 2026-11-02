"""평가 분모·누락·추가·중복·NULL 처리의 오류가 정확도를 부풀리지 않는지 확인한다."""

import importlib.util
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location("be007_benchmark", Path(__file__).with_name("benchmark.py"))
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)


def fixture_reference():
    fields = {field: b.fact() for field in b.FIELDS}
    fields["equipment_code"] = b.fact("TEST-001", ["E4"], "test")
    fields["version_raw"] = b.fact("vTEST.1", ["H4"], "test")
    reference = {"record_id": "Memory:E4", "sheet": "Memory", "equipment_cell": "E4",
        "equipment_code": "TEST-001", "pm_key": None, "raw": fields, "normalized": fields}
    sheet = {"sheet_name": "Memory", "first_row": 4, "first_col": 1,
             "values": [[None, None, None, None, "TEST-001", None, None, "vTEST.1"]], "merged_ranges": []}
    actual = {"values": {"equipment_code": "TEST-001", "version_raw": "vTEST.1"},
              "sources": {"equipment_code": "E4", "version_raw": "H4"}}
    return reference, sheet, actual


def test_missing_record_does_not_receive_credit_for_null_fields():
    reference, sheet, _ = fixture_reference()
    score, details = b.evaluate_stage([reference], [], sheet, "raw")
    assert score["evaluated_fields"] == 16 and score["correct_fields"] == 0
    assert score["accuracy_pct"] == 0 and score["missing_records"] == 1
    assert all(row["verdict"] == "missing_record" for row in details)


def test_perfect_record_counts_all_fields_and_nonempty_subset_separately():
    reference, sheet, actual = fixture_reference()
    score, _ = b.evaluate_stage([reference], [actual], sheet, "raw")
    assert score["correct_fields"] == 16 and score["accuracy_pct"] == 100
    assert score["nonempty_reference_fields"] == 2 and score["nonempty_accuracy_pct"] == 100
    assert score["null_reference_fields"] == 14


def test_duplicate_record_is_penalized_as_extra_record():
    reference, sheet, actual = fixture_reference()
    score, _ = b.evaluate_stage([reference], [actual, actual], sheet, "raw")
    assert score["evaluated_fields"] == 32 and score["correct_fields"] == 16
    assert score["accuracy_pct"] == 50 and score["extra_records"] == 1
    assert score["record_precision_pct"] == 50


def test_wrong_value_does_not_hide_behind_correct_provenance():
    reference, sheet, actual = fixture_reference()
    actual["values"]["equipment_code"] = "WRONG-CODE"
    score, details = b.evaluate_stage([reference], [actual], sheet, "raw")
    assert score["matched_records"] == 1 and score["correct_fields"] == 15
    cell = next(row for row in details if row["field"] == "equipment_code")
    assert cell["source_matches_reference"] is True and cell["correct"] is False


def test_invented_value_counts_against_null_reference():
    reference, sheet, actual = fixture_reference()
    actual["values"]["site"] = "invented-site"
    score, _ = b.evaluate_stage([reference], [actual], sheet, "raw")
    assert score["correct_fields"] == 15 and score["invented_values"] == 1


def test_unresolved_reference_is_excluded_with_coverage_reported():
    reference, sheet, actual = fixture_reference()
    reference["normalized"]["family_name"] = b.fact(None, ["D4"], "test", "unresolved")
    actual["values"]["family_name"] = "unconfirmed"
    score, details = b.evaluate_stage([reference], [actual], sheet, "normalized")
    assert score["evaluated_fields"] == 15 and score["correct_fields"] == 15
    assert score["unresolved_fields"] == 1 and score["reference_coverage_pct"] == 93.75
    assert next(row for row in details if row["field"] == "family_name")["verdict"] == "unresolved_returned_value"


@pytest.mark.parametrize("value", ["null", "None", ""])
def test_null_is_not_the_same_as_a_string(value):
    reference, sheet, actual = fixture_reference()
    actual["values"]["site"] = value
    score, _ = b.evaluate_stage([reference], [actual], sheet, "raw")
    assert score["correct_fields"] == 15


def test_unmerged_blank_line_is_not_forward_filled():
    sheet = {"first_row": 54, "first_col": 1, "values": [[None, "MFAB"], [None, None]], "merged_ranges": []}
    value, address = b.source_cell(sheet, "B", 55)
    assert value is None and address == "B55"


@pytest.mark.parametrize("actual", [{"values": [1]}, {"values": {"equipment_code": []}}, {"sources": ["E4"]}])
def test_malformed_row_maps_are_penalized_instead_of_crashing(actual):
    reference, sheet, _ = fixture_reference()
    score, _ = b.evaluate_stage([reference], [actual], sheet, "raw")
    assert score["missing_records"] == 1 and score["extra_records"] == 1
    assert score["accuracy_pct"] == 0
