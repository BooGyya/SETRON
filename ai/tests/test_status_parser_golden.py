# -*- coding: utf-8 -*-
"""골든테스트 v1 — A. 현황표 읽기 (A1~A20). 입력: 합성 데이터 v2 + 멘토 원본."""
import os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from svhm.excel.status_parser import parse_workbook, parse_status_rows, parse_block_label, split_versions

from conftest import H, U
HW = f"{H}/화성_설비현황표_SEMES_ETCH_교육용_2026-10-05.xlsx"
PT = f"{H}/평택설비현황표_교육용_2026-10-05.xlsx"
HW0 = f"{U}/화성_설비현황표_SEMES_ETCH_교육용.xlsx"
PT0 = f"{U}/평택설비현황표_교육용.xlsx"


@pytest.fixture(scope="module")
def hw(): return parse_workbook(HW)
@pytest.fixture(scope="module")
def pt(): return parse_workbook(PT)
@pytest.fixture(scope="module")
def hw0(): return parse_workbook(HW0)
@pytest.fixture(scope="module")
def pt0(): return parse_workbook(PT0)


def sheet(res, name): return next(x for x in res["results"] if x.sheet == name)
def rows_at(res, name, row): return [r for r in sheet(res, name).rows if r.source["row"] == row]


# A1 평택 P1: 한 행에 설비 2대 (Poly·Oxide)
def test_A1_side_by_side_two_tables(pt):
    s = sheet(pt, "P1")
    assert s.layout["segments"] == 2
    r = rows_at(pt, "P1", 3)
    assert [x.process_group for x in r] == ["Poly", "Oxide"]
    assert {x.equipment_code for x in r} == {"ESPA02", "ESOS21"}

# A2 평택 S5: 표 3개
def test_A2_three_tables(pt):
    s = sheet(pt, "S5")
    assert s.layout["segments"] == 3
    assert [x.process_group for x in rows_at(pt, "S5", 3)] == ["Poly", "Oxide", "HPA"]

# A3 블록 제목 → 라인/층/선언 대수, 불일치는 경고만
def test_A3_block_label_and_count_warning(pt):
    s = sheet(pt, "P1")
    b = s.blocks[0]
    assert (b.line, b.floor, b.declared_count) == ("P1F", "3F", 61)
    assert b.extracted_count != 61 and b.warning and "선언 대수" in b.warning
    assert any("P1F(3F)" in w for w in s.warnings)
    assert len(s.rows) > 0  # 저장은 막지 않음
    assert parse_block_label("S5\n( 3F )") == ("S5", "3F", None, "")
    assert parse_block_label("P1F(7F)\n(57)\n*신설비 이설 제외")[3] == "*신설비 이설 제외"

# A4 블록마다 반복되는 헤더 행은 설비로 읽지 않음
def test_A4_repeated_headers(pt):
    s = sheet(pt, "P1")
    assert s.layout["header_rows"] == [2, 17, 39]
    assert not any(r.equipment_code in ("설비호기", "No") for r in s.rows)
    assert len(s.blocks) == 3

# A5 표 제목 행(Poly/Oxide)은 설비 아님, 세그먼트 제목으로만
def test_A5_title_row_not_equipment(pt):
    s = sheet(pt, "P1")
    assert not any(r.source["row"] in (1, 16, 38) for r in s.rows)
    assert {r.process_group for r in s.rows} == {"Poly", "Oxide"}

# A6 화성 2줄 헤더 → Version/SW 열(H), Engine 제외
def test_A6_two_line_header_version_sw(hw):
    s = sheet(hw, "Memory")
    assert s.layout["two_line_header"] is True
    assert s.layout["columns"]["version"] == 8
    assert rows_at(hw, "Memory", 8)[0].version_raw == "v4.56.6.42"

# A7 화성 Foundry: No. 열 없음 → 버전 열 G
def test_A7_column_shift(hw):
    s = sheet(hw, "Foundry")
    assert s.layout["columns"]["version"] == 7 and s.layout["columns"]["equipment"] == 4
    assert "no" not in s.layout["columns"]
    assert rows_at(hw, "Foundry", 42)[0].version_raw == "V3.52_F14"

# A8 Line 병합 fill-down
def test_A8_line_filldown(hw):
    assert [r.line for r in rows_at(hw, "Memory", 5) + rows_at(hw, "Memory", 6) + rows_at(hw, "Memory", 7)] == ["13L"] * 3
    assert rows_at(hw, "Memory", 4)[0].line == "NRD (MR2동)"

# A9 시트명 → production_area
def test_A9_production_area(hw, pt):
    assert rows_at(hw, "Memory", 4)[0].production_area == "MEMORY"
    assert rows_at(hw, "Foundry", 4)[0].production_area == "FOUNDRY"
    assert rows_at(pt, "P1", 3)[0].production_area == "MEMORY"
    assert rows_at(pt, "S5", 3)[0].production_area == "FOUNDRY"

# A10 비고 메모는 설비로 읽지 않고 보존
def test_A10_notes_preserved(hw):
    s = sheet(hw, "Memory")
    assert all(r.equipment_code for r in s.rows)
    r = rows_at(hw, "Memory", 6)[0]
    assert any("구UI" in x for x in r.extras.get("unlabeled", []) + [v for v in r.extras.values() if isinstance(v, str)])

# A11 버전 칸 글자 '메모리버전임' → VERSION_TEXT + override 힌트
def test_A11_version_text(hw):
    r = rows_at(hw, "Foundry", 5)[0]
    assert "VERSION_TEXT" in r.issues and r.versions == [] and r.version_raw == "메모리버전임"
    assert r.extras.get("area_override_hint") == "MEMORY"

# A12 빈 버전 칸 → 설비는 읽고 VERSION_MISSING
def test_A12_version_missing(hw):
    r = rows_at(hw, "Foundry", 20)[0]
    assert r.equipment_code == "ESPGR76" and "VERSION_MISSING" in r.issues

# A13 설비호기 꼬리말 (JEP)
def test_A13_equipment_tail(hw0):
    r = [x for x in sheet(hw0, "Memory").rows if x.equipment_code == "ESJOF06"][0]
    assert r.equipment_tail == "(JEP)" and "EQUIPMENT_TAIL" in r.issues

# A14 같은 설비호기 두 번 → 중복 오류, 양쪽 표시
def test_A14_duplicate_equipment(pt):
    dups = dict(pt["duplicates"])
    assert "ESPA02" in dups and {d["sheet"] for d in dups["ESPA02"]} == {"P1", "P2"}
    for res in pt["results"]:
        for r in res.rows:
            if r.equipment_code == "ESPA02":
                assert "DUPLICATE_EQUIPMENT" in r.issues

# A15 HPA → 읽되 집계 제외 표시
def test_A15_hpa_excluded(pt):
    hpa = [r for r in sheet(pt, "S5").rows if r.group_raw == "HPA"]
    assert len(hpa) == 5 and all(r.issues == ["ANALYSIS_EXCLUDED"] for r in hpa)   # 버전 비어 있어도 '누락' 아님

# A16 완전 빈 행 건너뜀
def test_A16_empty_rows_skipped(hw):
    s = sheet(hw, "Foundry")
    assert s.skipped["empty_row"] >= 4
    assert not any(r.source["row"] in (36, 38, 39, 40, 53) for r in s.rows)

# A17 한 칸 두 버전 (PM 혼합)
def test_A17_multi_version(hw):
    r = rows_at(hw, "Foundry", 57)[0]
    assert "MULTI_VERSION" in r.issues
    assert [(v.label, v.text) for v in r.versions] == [("O3", "v4.50.1.1_a"), ("O2", "v3.53_F2")]
    assert r.group_raw == "M-O2/O3"

# A18 끝 공백 → 조용히 제거 (꼬리표 없음)
def test_A18_trailing_space(pt):
    r = [x for x in rows_at(pt, "P1", 5) if x.process_group == "Poly"][0]
    assert r.version_raw == "v3.53_1" and r.versions[0].text == "v3.53_1"
    assert r.issues == []

# A19 대문자 → 원문 보존 (정규화는 매칭 단계)
def test_A19_uppercase_preserved(pt):
    r = [x for x in rows_at(pt, "P1", 19) if x.process_group == "Oxide"][0]
    assert r.version_raw == "V3.53_2"

# A20 헤더 없는 시트 → 예외 없이 needs_ai_mapping
def test_A20_no_header_needs_ai(pt0):
    s = sheet(pt0, "P2")   # 멘토 원본 자리표시 시트 ('P1과 유사')
    assert s.needs_ai_mapping is True and s.rows == []
    res = parse_status_rows([["아무거나", "글자"], ["x", "y"]], "임의")
    assert res.needs_ai_mapping is True

# 보너스: 멘토 원본(설비호기·버전 비어 있음)은 설비 슬롯만 세고 저장 대상 없음
def test_bonus_unfilled_original(pt0):
    s = sheet(pt0, "P1")
    assert s.rows == [] and s.skipped["no_equipment_slot"] > 50

# 보너스: 옛 교육용 단순 양식(라인/설비군/설비명/현재 SW VER)도 읽힘
def test_bonus_flat_format():
    res = parse_workbook(f"{U}/SEMES_설비현황표__2___1_.xlsx", sheets=["설비현황"])
    s = res["results"][0]
    assert len(s.rows) == 100 and s.layout["columns"]["version"] == 4

def test_split_versions():
    assert split_versions("")[1] == ["VERSION_MISSING"]
    assert split_versions("메모리버전임")[1] == ["VERSION_TEXT"]
    assert [v.text for v in split_versions("v4.56.6.42")[0]] == ["v4.56.6.42"]
