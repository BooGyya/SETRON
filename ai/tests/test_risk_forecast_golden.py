# -*- coding: utf-8 -*-
"""골든테스트 v1 — E6~E10: 위험 판정, 노후도 색, 예측, 우선순위."""
import os, sys
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from svhm.core.risk import RiskRule, AgeColor, assess_all, priority_list
from svhm.core.forecast import SnapshotPoint, forecast
from svhm.core.pipeline import match_equipment
from svhm.excel.status_parser import EquipmentRow, VersionEntry

def row(group_raw, version, code="ESXX01"):
    return EquipmentRow("평택", "P1", None, "P1F", None, None, group_raw, code, code, None, version,
                        [VersionEntry("", version)] if version else [], "MEMORY", {"sheet": "P1", "row": 0, "col_equipment": 0, "col_version": 0}, [], {})

AS_OF = date(2026, 10, 5)

# E6 환경안전 아이템 등록 → 그 버전 미만 설비 전부 위험
def test_E6_safety_item_rule(registry):
    rule = RiskRule(1, "SAFETY_REQUIRED", 5, "v4.56.6.42", None, "환경안전", "GAS MIX Interlock")
    ms = [match_equipment(row("M-C4", v, f"E{i}"), registry) for i, v in enumerate(["v4.56.6.41_d5", "v4.56.6.42", "v4.56.6.43_b"])]
    rs = assess_all(ms, registry, [rule], AS_OF)
    assert [r.is_risk for r in rs] == [True, False, False]
    assert "미적용" in rs[0].reasons[0] and "3칸 아래" in rs[0].reasons[0]   # 41_d5 → 41_e → 41_f5a → 42

# E7 결함 버전 등록 → 그 버전(구간) 쓰는 설비 위험
def test_E7_defect_version_rule(registry):
    closed = RiskRule(2, "DEFECT", 5, "v4.56.6.43", "v4.56.7.1_a", "결함", "")
    ms = [match_equipment(row("M-C4", v, f"E{i}"), registry) for i, v in enumerate(["v4.56.6.42", "v4.56.6.43", "v4.56.6.43_b", "v4.56.7.1_a"])]
    assert [r.is_risk for r in assess_all(ms, registry, [closed], AS_OF)] == [False, True, True, False]
    open_ended = RiskRule(3, "DEFECT", 5, "v4.56.6.43", None, "결함", "")
    assert [r.is_risk for r in assess_all(ms, registry, [open_ended], AS_OF)] == [False, True, True, True]

# E7b 규칙은 같은 줄기(DFB)만 판정, 다른 계열엔 영향 없음
def test_E7b_rule_scope(registry):
    rule = RiskRule(4, "SAFETY_REQUIRED", 5, "v4.56.6.42", None, "환경안전", "", variant="")
    ms = [match_equipment(row("M-C4(DFB)", "v4.56.6.41_e_DFB", "D1"), registry), match_equipment(row("M-O2", "v3.52_10", "O1"), registry)]
    assert [r.is_risk for r in assess_all(ms, registry, [rule], AS_OF)] == [False, False]

# E8 노후도 색: 1년 미만 초록 / 1년 이상 주황 / 2년 이상 빨강 (설정값)
def test_E8_age_color():
    ac = AgeColor()
    assert ac.color(date(2026, 1, 1), date(2026, 10, 5))[0].startswith("초록")
    assert ac.color(date(2025, 6, 1), date(2026, 10, 5))[0].startswith("주황")
    assert ac.color(date(2024, 9, 1), date(2026, 10, 5))[0].startswith("빨강")
    assert AgeColor((180, 365)).color(date(2026, 1, 1), date(2026, 10, 5))[0].startswith("주황")   # 기준 바꾸면 달라짐
    assert ac.color(None, date(2026, 10, 5))[0].startswith("회색")

# E9 예측: 9/1 4대 → 10/5 6대, 전체 10대 → 2대/34일 → 남은 4대 ≈ 68일, '참고용(데이터 2장)'
def test_E9_forecast():
    fc = forecast([SnapshotPoint(date(2026, 9, 1), 4, 10), SnapshotPoint(date(2026, 10, 5), 6, 10)])
    assert fc.status == "OK" and fc.remaining == 4 and fc.est_days == 68 and fc.est_date == date(2026, 12, 12)
    assert "데이터 2장" in fc.message
    assert forecast([SnapshotPoint(date(2026, 10, 5), 6, 10)]).status == "NOT_ENOUGH_DATA"
    assert forecast([SnapshotPoint(date(2026, 9, 1), 6, 10), SnapshotPoint(date(2026, 10, 5), 6, 10)]).status == "NO_PROGRESS"
    assert forecast([SnapshotPoint(date(2026, 9, 1), 9, 10), SnapshotPoint(date(2026, 10, 5), 10, 10)]).status == "DONE"

# E10 우선순위: 위험 → 가장 낮은 버전 → 낮은 순 (동률은 출시일 오래된 순)
def test_E10_priority(registry):
    rule = RiskRule(5, "SAFETY_REQUIRED", 5, "v4.56.6.43", None, "환경안전", "GAS MIX")
    ms = [match_equipment(row("M-C4", v, c), registry) for c, v in [("A", "v4.56.7.1_a"), ("B", "v4.56.6.42"), ("C", "v4.56.6.41_d5"), ("D", "v4.56.9.1")]]
    n_by = {(5, ""): "v4.59.0.2"}
    pr = priority_list(assess_all(ms, registry, [rule], AS_OF), registry, n_by)
    assert [er.match.equipment_code for _, er, _ in pr] == ["C", "B", "A", "D"]   # C·B 위험(많이 뒤처진 순), 그다음 A, D
    assert pr[0][2].startswith("위험") and "기준까지" in pr[2][2]
    # 릴리즈노트가 다른 설비끼리는 '기준 N 까지 남은 칸 수'로 비교
    ms2 = [match_equipment(row("M-C4", "v4.59.0.1_a", "X"), registry), match_equipment(row("M-O2", "v3.52_10", "Y"), registry)]
    pr2 = priority_list(assess_all(ms2, registry, [], AS_OF), registry, {(5, ""): "v4.59.0.2", (7, ""): "v3.53_2"})
    assert [er.match.equipment_code for _, er, _ in pr2] == ["Y", "X"]
