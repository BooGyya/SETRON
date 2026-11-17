# -*- coding: utf-8 -*-
"""위험 판정 + 노후도 색 + 우선순위 + 통일 예측 데모.
    python demo_risk.py --status <현황표 폴더> --release <릴리즈노트 폴더> [--mentor <멘토 원본 폴더>] [--site 평택|화성] [--asof 2026-10-05] [--out 결과폴더]
현황표 폴더에서 '<사이트>…_YYYY-MM-DD.xlsx' 들을 날짜순으로 읽어 예측에 쓴다. 위험 규칙은 아래 DEMO_RULES (실제론 사용자가 화면에서 등록).
"""
import csv, glob, os, re, sys
from collections import Counter, defaultdict
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svhm.core import refdata as R
from svhm.core.pipeline import build_registry, match_equipment, summarize
from svhm.core.risk import RiskRule, AgeColor, assess_all, priority_list, steps_below_target
from svhm.core.forecast import SnapshotPoint, forecast
from svhm.excel.status_parser import parse_workbook

args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = args[i + 1]; del args[i:i + 2]; return v
    return default
status_dir = opt("--status", "."); rel = opt("--release", "."); mentor = opt("--mentor", rel)
site = opt("--site", "평택"); as_of = date.fromisoformat(opt("--asof", "2026-10-05")); out_dir = opt("--out", "parsed_risk")
os.makedirs(out_dir, exist_ok=True)
F = lambda d, n: os.path.join(d, n)
MAP = {1: (F(rel, "P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx"), "구UI"), 2: (F(rel, "P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx"), "통합UI"),
       4: (F(rel, "P_PJT_M-C3_Foundry_SW_Release_List_교육용.xlsx"), "통합UI"), 5: (F(mentor, "P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx"), None),
       6: (F(rel, "P_PJT_M-C4_Foundry_SW_Release_List_교육용.xlsx"), None), 7: (F(rel, "P_PJT_M-O2_SW_정기_Release_List_교육용.xlsx"), "Memory"),
       8: (F(rel, "P_PJT_M-O2_Foundry_SW_Release_List_교육용.xlsx"), "Foundry"), 9: (F(rel, "P_PJT_M-O3_SW_정기_Release_List_교육용.xlsx"), None),
       10: (F(mentor, "P_PJT_M-O5_SW_정기_Release_List_교육용.xlsx"), None), 11: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Inline Ox"),
       12: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Inline O2"), 13: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Cluster Ox"),
       14: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Cluster O2")}
reg = build_registry({k: v for k, v in MAP.items() if os.path.exists(v[0])})
if getattr(reg, "skipped", {}):
    print("!!! 못 읽은 릴리즈노트:", reg.skipped)

# ---- 데모용 위험 규칙 (실제 운영에선 사용자가 '위험 아이템/버전 등록' 화면에서 입력 → risk_rule 테이블)
DEMO_RULES = [
    RiskRule(1, "SAFETY_REQUIRED", 5, "v4.56.6.43", None, "환경안전 필수", "환경안전 관련 GAS MIX Interlock 상태 표시 (C4 v4.56.6.43, No.1)"),
    RiskRule(2, "SAFETY_REQUIRED", 7, "v3.52_12", None, "환경안전 필수", "O2 메모리 v3.52_12 환경안전 항목(데모)"),
    RiskRule(3, "DEFECT", 2, "v3.52_13_a2", "v3.52_13_e", "결함(핫픽스 필요) — 데모", "C3 통합UI 13_a2~13_d 결함"),
]
# ---- 기준 N = 각 릴리즈노트 최신 칸
n_by = {}
for fid, lad in reg.ladders.items():
    for var in {r.variant for r in lad.rungs}:
        n_by[(fid, var)] = sorted(lad.by_variant(var), key=lambda r: r.lineage_order)[-1].name

# ---- 날짜별 현황표 읽기
files = sorted(glob.glob(os.path.join(status_dir, f"*{site}*_20??-??-??.xlsx")) + glob.glob(os.path.join(status_dir, f"{site}*_20??-??-??.xlsx")))
files = sorted(set(files))
snap = {}
for f in files:
    d = date.fromisoformat(re.search(r"(\d{4}-\d{2}-\d{2})", f).group(1))
    res = parse_workbook(f)
    snap[d] = [match_equipment(r, reg) for s in res["results"] for r in s.rows]
print(f"[{site}] 현황표 {len(snap)}장: {', '.join(d.isoformat() for d in sorted(snap))}")
latest_date = max(snap); latest = snap[latest_date]

# ---- 위험 판정 (최신 스냅샷)
risks = assess_all(latest, reg, DEMO_RULES, as_of)
n_risk = sum(1 for r in risks if r.is_risk)
print(f"\n■ 위험 설비 ({as_of} 기준): {n_risk}대 / 대상 {len(risks)}대")
by_rule = Counter(rid for r in risks for v in r.per_version for rid in v.rule_ids)
for rule in DEMO_RULES:
    print(f"   규칙 {rule.rule_id} [{rule.rule_type}] {R.FAMILIES[rule.family_id]['name']} {rule.start_version}{(' ~ ' + rule.end_version) if rule.end_version else ''}: {by_rule.get(rule.rule_id, 0)}대 — {rule.item_ref}")
print("■ 노후도 색 분포:", ", ".join(f"{k} {v}대" for k, v in Counter(r.worst_color for r in risks).most_common()))

# ---- 우선순위
pr = priority_list(risks, reg, n_by)
print("\n■ 업그레이드 우선순위 상위 10:")
for rank, er, why in pr[:10]:
    m = er.match
    print(f"   {rank:>2}. {m.equipment_code} ({m.sheet}/{m.line}, {m.group_name}) — {why[:110]}")

# ---- 예측 (계열별, N = 최신 칸)
print("\n■ 버전 통일 예측 (기준 N = 각 릴리즈노트 최신 칸):")
fam_names = sorted({v.family_name for m in latest for v in m.versions})
for fam in fam_names:
    pts = []
    for d, ms in sorted(snap.items()):
        sel = [m for m in ms if any(v.family_name == fam for v in m.versions)]
        s = summarize(sel, reg, n_by)
        pts.append(SnapshotPoint(d, s.applied, s.total))
    fc = forecast(pts)
    series = " → ".join(f"{p.base_date.strftime('%m/%d')} {p.applied}/{p.total}" for p in pts)
    print(f"   {fam}: {series} | {fc.message}")

# ---- CSV
with open(f"{out_dir}/{site}_위험판정_{latest_date}.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["순위", "설비호기", "시트", "라인", "설비군", "릴리즈노트", "버전", "기준 N", "N까지 칸 수", "위험", "사유", "노후도 색", "출시 후 일수"])
    for rank, er, why in pr:
        m = er.match
        for v, vr in zip(m.versions, er.per_version):
            n_name = n_by.get((v.family_id, v.result.rung.variant)) if v.result.rung else ""
            st = steps_below_target(v, reg, n_by)
            w.writerow([rank, m.equipment_code, m.sheet, m.line, m.group_name, v.family_name, v.result.rung.name if v.result.rung else v.result.input_text,
                        n_name or "", st if st is not None else "", "예" if vr.is_risk else "", " / ".join(vr.reasons), vr.age_color, vr.age_days if vr.age_days is not None else ""])
print(f"\n저장: {out_dir}/{site}_위험판정_{latest_date}.csv")
