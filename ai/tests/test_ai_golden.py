# -*- coding: utf-8 -*-
"""AI 세 가지 — 오프라인(mock) 테스트. 실제 LLM 정답률은 eval_query.py 로 잰다."""
import os, sys, json
os.environ["SVHM_LLM_MOCK"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import H
from svhm.ai.llm_client import LLMClient, LLMSettings, extract_json, strip_code_fence
from svhm.ai.query_parser import parse_query, QueryCondition
from svhm.ai.column_mapper import ask_profile, apply_status_profile, transpose_horizontal_release, ImportProfile
from svhm.ai.report_writer import write_report, check_numbers, Report
from svhm.excel.status_parser import parse_status_rows, rows_from_openpyxl
from svhm.excel.release_parser import parse_release_rows

def test_llm_settings_switch_by_env(monkeypatch):
    s = LLMSettings(base_url="http://10.0.0.1:8000/v1", api_key="EMPTY", model="gpt-oss-120b", param_style="classic")
    assert "gpt-oss-120b" in s.describe() and "10.0.0.1" in s.describe()

def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('여기 결과입니다: {"a": [1,2]} 끝') == {"a": [1, 2]}
    assert strip_code_fence("```\n{}\n```") == "{}"

# E2 자연어 → 조회 조건
def test_E2_query_examples_mock():
    ex = json.load(open(os.path.join(os.path.dirname(__file__), "..", "svhm", "ai", "query_examples.json"), encoding="utf-8"))
    qc = parse_query(ex[0]["q"])
    assert qc.sites == ["평택"] and qc.groups == ["O2"] and qc.relative.op == "steps_below_at_least" and qc.relative.steps == 2
    assert parse_query("Inline O2 설비 현황").groups == ["KCTC Inline O2"]        # O2 ≠ KCTC Inline O2
    assert parse_query("C5 설비 현황 보여줘").need_clarification
    assert parse_query("KCTC는 언제 다 올라가?").intent == "forecast"

def test_E2_schema_rejects_unknown_vocab():
    qc = QueryCondition(sites=["구미", "평택"], groups=["C5", "C4"])
    assert qc.sites == ["평택"] and qc.groups == ["C4"]

# E1 AI 열 매핑
def test_E1_horizontal_release_via_transpose():
    rows = rows_from_openpyxl(f"{H}/P_PJT_M-O2_SW_정기_Release_List_교육용_가로형_AI테스트용.xlsx", "Memory")
    assert parse_release_rows(rows, "Memory").needs_ai_mapping
    prof = ask_profile(rows)
    assert prof.doc_type == "RELEASE" and prof.orientation == "horizontal" and prof.header_row == 3
    res = parse_release_rows(transpose_horizontal_release(rows, prof.header_row), "Memory")
    assert len(res.versions) == 24 and res.ladder("")[0] == "v3.52_10"

def test_E1_status_profile_rewrites_header():
    rows = [list(r) for r in rows_from_openpyxl(f"{H}/화성_설비현황표_SEMES_ETCH_교육용_2026-10-05.xlsx", "Memory")]
    rows[1] = ["구분", "구역", "순번", "모델명", "장비 ID", "위치", None, "현재 SW", None, "CTC", "EPD", None, "개조", None, None, None]
    rows[2] = [None] * 16
    assert parse_status_rows(rows, "Memory").needs_ai_mapping
    prof = ImportProfile(doc_type="STATUS", header_row=2, column_map={"line": 2, "no": 3, "group": 4, "equipment": 5, "version": 8})
    res = parse_status_rows(apply_status_profile(rows, prof), "Memory")
    assert not res.needs_ai_mapping and len(res.rows) == 85 and res.layout["columns"]["version"] == 8

def test_E1_placeholder_sheet_profile():
    prof = ask_profile([["P2F"], [None, None, "P1과 유사"], ["P2D"]])
    assert prof.doc_type == "UNKNOWN" and prof.header_row is None

# E3 보고서
def test_E3_report_mock_and_number_check():
    data = {"title": "t", "summary": {"total": 41, "applied": 12, "need_upgrade": 29, "unregistered": 0, "rate": 29.3}, "forecast": {"points": 3}}
    rep = write_report(data)
    assert "41" in rep.summary and rep.number_warnings == []
    fake = Report(title="t", summary="대상 999대", sections=[], cautions=[])
    assert any("999" in w for w in check_numbers(fake, data))
