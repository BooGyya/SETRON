# -*- coding: utf-8 -*-
"""A+B+C+D 통합 데모: 현황표 → 설비마다 '어느 릴리즈노트 몇 번째 칸' + 기준 버전 N 으로 적용률.
    python demo_match.py <현황표.xlsx> [...] --release <릴리즈노트 폴더> [--mentor <멘토 원본 폴더>] [--out 결과폴더]
릴리즈노트 폴더엔 합성 데이터 v2 파일들, 멘토 원본 폴더엔 C4·O5 원본(없으면 릴리즈노트 폴더에서 찾음).
"""
import csv, json, os, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svhm.core import refdata as R
from svhm.core.pipeline import build_registry, match_equipment, summarize
from svhm.excel.status_parser import parse_workbook, issue_text

args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = args[i + 1]; del args[i:i + 2]; return v
    return default
out_dir = opt("--out", "parsed_match"); rel = opt("--release", "."); mentor = opt("--mentor", rel)
os.makedirs(out_dir, exist_ok=True)
F = lambda d, n: os.path.join(d, n)
MAP = {
    1: (F(rel, "P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx"), "구UI"), 2: (F(rel, "P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx"), "통합UI"),
    4: (F(rel, "P_PJT_M-C3_Foundry_SW_Release_List_교육용.xlsx"), "통합UI"),
    5: (F(mentor, "P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx"), None), 6: (F(rel, "P_PJT_M-C4_Foundry_SW_Release_List_교육용.xlsx"), None),
    7: (F(rel, "P_PJT_M-O2_SW_정기_Release_List_교육용.xlsx"), "Memory"), 8: (F(rel, "P_PJT_M-O2_Foundry_SW_Release_List_교육용.xlsx"), "Foundry"),
    9: (F(rel, "P_PJT_M-O3_SW_정기_Release_List_교육용.xlsx"), None), 10: (F(mentor, "P_PJT_M-O5_SW_정기_Release_List_교육용.xlsx"), None),
    11: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Inline Ox"), 12: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Inline O2"),
    13: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Cluster Ox"), 14: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Cluster O2"),
}
MAP = {k: v for k, v in MAP.items() if os.path.exists(v[0])}
reg = build_registry(MAP)
print("릴리즈노트(사다리) 읽음:", ", ".join(f"{R.FAMILIES[k]['name']} {len(v.rungs)}칸" for k, v in reg.ladders.items()))
missing_fams = [fid for fid in R.FAMILIES if fid not in reg.ladders and not R.FAMILIES[fid].get("unconfirmed")]
if missing_fams:
    print("!!! 사다리를 못 만든 릴리즈노트가 있어요 — 해당 설비군은 전부 '릴리즈노트 없음'으로 나옵니다:")
    for fid in missing_fams:
        print(f"    - {R.FAMILIES[fid]['name']}: {getattr(reg, 'skipped', {}).get(fid, '매핑 목록(MAP)에 없음')}")
STATUS_KO = {"MATCHED": "연결됨", "UNREGISTERED": "미등록", "MISSING": "버전 누락", "AMBIGUOUS": "후보 선택 필요",
             "GROUP_UNKNOWN": "설비군 확인 필요", "EXCLUDED": "집계 제외", "NO_LADDER": "릴리즈노트 없음"}
VSTAT_KO = {"EXACT": "일치", "NORMALIZED": "표기 보정", "AMBIGUOUS": "후보 여러 개", "UNREGISTERED": "미등록", "MISSING": "누락", "NO_LADDER": "릴리즈노트 없음"}

for path in args:
    res = parse_workbook(path)
    matches = [match_equipment(r, reg) for s in res["results"] for r in s.rows]
    stem = os.path.splitext(os.path.basename(path))[0]
    print(f"\n[{os.path.basename(path)}] 설비 {len(matches)}대")
    print("  결과:", ", ".join(f"{STATUS_KO.get(k, k)} {v}대" for k, v in Counter(m.overall for m in matches).most_common()))
    rows = []
    for m in matches:
        if not m.versions:
            rows.append({"사이트": m.site, "시트": m.sheet, "라인": m.line, "설비호기": m.equipment_code, "설비군(원문)": m.group_raw,
                         "설비군": m.group_name, "메모리/파운드리": m.area, "릴리즈노트": "", "PM": "", "버전(원문)": "", "연결된 칸": "",
                         "순서": "", "상태": STATUS_KO.get(m.overall, m.overall), "설명": " / ".join(m.messages), "원본": f"{m.sheet} {m.source['row']}행"})
        for v in m.versions:
            rows.append({"사이트": m.site, "시트": m.sheet, "라인": m.line, "설비호기": m.equipment_code, "설비군(원문)": m.group_raw,
                         "설비군": R.GROUPS[v.group_id]["name"] if v.group_id else m.group_name, "메모리/파운드리": m.area,
                         "릴리즈노트": v.family_name, "PM": v.label, "버전(원문)": v.result.input_text,
                         "연결된 칸": v.result.rung.name if v.result.rung else "", "순서": v.result.rung.order if v.result.rung else "",
                         "상태": VSTAT_KO.get(v.result.status, v.result.status), "설명": v.result.message + (" / " + " / ".join(m.messages) if m.messages else ""),
                         "원본": f"{m.sheet} {m.source['row']}행"})
    with open(f"{out_dir}/{stem}_매칭결과.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    with open(f"{out_dir}/{stem}_매칭결과.json", "w", encoding="utf-8") as f:
        json.dump([m.to_dict() for m in matches], f, ensure_ascii=False, indent=1, default=str)
    # 확인 필요 목록
    need = [m for m in matches if m.overall not in ("MATCHED", "EXCLUDED")]
    print(f"  확인 필요 {len(need)}대:")
    for m in need:
        print(f"    - {m.sheet} {m.source['row']}행 {m.equipment_code} ({m.group_raw}) → {STATUS_KO.get(m.overall)}: {' / '.join(m.messages)[:120]}")
    # 기준 버전 N = 각 릴리즈노트의 맨 위 칸(최신)으로 두고 적용률 예시
    n_by = {}
    for fid, lad in reg.ladders.items():
        for var in {r.variant for r in lad.rungs}:
            n_by[(fid, var)] = sorted(lad.by_variant(var), key=lambda r: r.lineage_order)[-1].name
    by_fam = defaultdict(list)
    for m in matches:
        for v in m.versions:
            by_fam[v.family_name].append(m)
    print("  기준 버전 N = 각 릴리즈노트 최신 칸으로 둘 때 적용률:")
    for fam_name in sorted(by_fam):
        ms = list({id(m): m for m in by_fam[fam_name]}.values())
        s = summarize([m for m in ms], reg, n_by)
        print(f"    {fam_name}: 대상 {s.total}대, 적용 {s.applied}, 업그레이드 필요 {s.need_upgrade}, 미등록 {s.unregistered}, 누락 {s.missing}"
              + (f", 기준 없음 {s.no_n}" if s.no_n else "") + (f" → 적용률 {s.rate()}%" if s.rate() is not None else ""))
    print(f"  저장: {out_dir}/{stem}_매칭결과.csv / .json")
