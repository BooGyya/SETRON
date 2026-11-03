# -*- coding: utf-8 -*-
"""골든테스트 v1 — B. 릴리즈노트 읽기 (B1~B11)."""
import os, sys
from datetime import date
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from svhm.excel.release_parser import parse_release_workbook, parse_release_rows, split_version_cell, split_variant

from conftest import H, U

@pytest.fixture(scope="module")
def c4(): return parse_release_workbook(f"{U}/P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx")[0]
@pytest.fixture(scope="module")
def o5(): return parse_release_workbook(f"{U}/P_PJT_M-O5_SW_정기_Release_List_교육용.xlsx")[0]
@pytest.fixture(scope="module")
def o3(): return parse_release_workbook(f"{H}/P_PJT_M-O3_SW_정기_Release_List_교육용.xlsx")[0]
@pytest.fixture(scope="module")
def c3(): return {r.sheet: r for r in parse_release_workbook(f"{H}/P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx")}
@pytest.fixture(scope="module")
def kctc(): return {r.sheet: r for r in parse_release_workbook(f"{H}/P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx")}
@pytest.fixture(scope="module")
def c4f(): return parse_release_workbook(f"{H}/P_PJT_M-C4_Foundry_SW_Release_List_교육용.xlsx")[0]

def ver(res, name): return next(v for v in res.versions if v.name == name)

# B1 헤더 행 자동 탐지
def test_B1_header_detection(c4, o5, o3):
    assert (c4.header_row, o5.header_row, o3.header_row) == (2, 3, 4)
    assert c4.columns["version"] == 1 and c4.columns["content"] == 4 and o5.columns["content"] == 5

# B2 한 셀 두 버전 → 항목 공유, 날짜
def test_B2_multi_version_cell(c4):
    a, b = ver(c4, "v4.56.6.42"), ver(c4, "v4.56.6.42_DFB")
    assert a.item_rows == b.item_rows and len(a.item_rows) == 18
    assert a.release_date == b.release_date == date(2026, 1, 23)
    assert (b.base, b.variant) == ("v4.56.6.42", "DFB")
    assert len(c4.versions) == 36 and len(c4.ladder("")) == 27 and len(c4.ladder("DFB")) == 9

# B3 NormalC4/DFB 플래그 → 적용 범위
def test_B3_applies_to(c4):
    assert c4.flag_columns == {"NORMAL": 7, "DFB": 8}
    items = {i.source_row: i for i in c4.items}
    assert items[44].applies_to == ["NORMAL"]          # O / (빈칸)
    assert items[46].applies_to == ["NORMAL"]          # O / X
    assert items[43].applies_to == ["UNKNOWN"]         # 빈칸 / 빈칸
    assert any(i.applies_to == ["DFB"] for i in c4.items)
    assert any(i.applies_to == ["NONE"] for i in c4.items)   # X / X

# B4 O3: 헤더 4행, 'Ver.'·'변경사항', 날짜 별도 열
def test_B4_separate_date_column(o3):
    assert o3.columns["version"] == 1 and o3.columns["date"] == 2 and o3.columns["content"] == 5
    assert o3.versions[0].release_date == date(2026, 1, 6)
    assert ver(o3, "v4.49.62.77_d10").release_date == date(2026, 4, 27)

# B5 KCTC 점 구분 날짜
def test_B5_dot_date(kctc):
    assert kctc["Inline Ox"].versions[0].release_date == date(2026, 1, 5)
    assert kctc["Cluster Ox"].ladder("")[3] == "v999.3_7"

# B6 '+ Hotfix' 는 버전 이름 그대로
def test_B6_hotfix_literal_name(c3):
    v = ver(c3["구UI"], "v1.1.1.6B_6 + Hotfix")
    assert v.release_date == date(2026, 3, 8) and v.variant == ""
    assert "(H)" not in " ".join(x.name for x in c3["통합UI"].versions)

# B7 _DEX 파생 분리
def test_B7_dex_variant(c4f):
    v = ver(c4f, "v4.56.8.1_f1_DEX")
    assert (v.base, v.variant) == ("v4.56.8.1_f1", "DEX")
    assert c4f.ladder("DEX") == ["v4.56.8.1_f1_DEX", "v4.57.2.2_f1_DEX"]
    assert split_variant("v4.56.6.42_DFB") == ("v4.56.6.42", "DFB")

# B8 'Prime' 항목 표시 (멘토 샘플엔 없어 구성 입력으로 검증)
def test_B8_prime_flag():
    rows = [["P-PJT M-O5"], ["버전명", "No", "항목", "Update내용"],
            ["v4.60.71.1_a (2026-05-01)", 1, "UI", "Prime 설비 전용 Recipe 화면 개선"],
            [None, 2, "PMC", "Gas Flow 안정화 판정 개선"]]
    r = parse_release_rows(rows, "O5")
    assert [i.is_prime for i in r.items] == [True, False]

# B9 P0 / '환경안전' → 위험 아이템 후보
def test_B9_safety_candidate(c4):
    p0 = [i for i in c4.items if i.priority == "P0"]
    assert len(p0) == 13 and all(i.is_safety_candidate for i in p0)
    assert any("환경안전" in i.content and i.is_safety_candidate for i in c4.items)

# B10 가로형 → AI 열 매핑 필요 (예외 아님)
def test_B10b_o2_vertical_now_parses():
    r = parse_release_workbook(f"{H}/P_PJT_M-O2_SW_정기_Release_List_교육용.xlsx")[0]
    assert not r.needs_ai_mapping and len(r.versions) == 24

def test_B10_horizontal_needs_ai():
    r = parse_release_workbook(f"{H}/P_PJT_M-O2_SW_정기_Release_List_교육용_가로형_AI테스트용.xlsx")[0]
    assert r.needs_ai_mapping and "가로" in r.hint and r.versions == []

# B11 순서 = 나열 순서, 날짜 역전 경고
def test_B11_order_and_date_warning(o3):
    assert [v.order for v in o3.versions[:3]] == [10, 20, 30] and o3.warnings == []
    rows = [["t"], ["버전명", "No", "항목", "Update내용"],
            ["v2.0 (2026-03-01)", 1, "UI", "a"], ["v1.0 (2026-01-01)", 1, "UI", "b"]]
    r = parse_release_rows(rows, "x")
    assert any("빠름" in w for w in r.warnings)

def test_split_version_cell():
    assert split_version_cell("v4.56.6.42, v4.56.6.42 _DFB (2026-01-23)") == (["v4.56.6.42", "v4.56.6.42_DFB"], date(2026, 1, 23))
    assert split_version_cell("v874.22_b (2026.02.16)") == (["v874.22_b"], date(2026, 2, 16))
    assert split_version_cell("v1.1.1.6B_6 + Hotfix (2026-03-08)")[0] == ["v1.1.1.6B_6 + Hotfix"]
