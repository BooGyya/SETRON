# -*- coding: utf-8 -*-
"""AI 세 가지 데모. 설정은 .env (SVHM_LLM_*). SVHM_LLM_MOCK=1 이면 서버 없이 흉내.
  python demo_ai.py query "평택 O2 중 두 칸 이상 뒤처진 설비만 보여줘"
  python demo_ai.py map   <엑셀> [시트]            # 규칙 파서가 못 읽는 시트 → AI 열 매핑 → 다시 읽기
  python demo_ai.py report --status <폴더> --release <폴더> --mentor <폴더> --site 평택 [--family "O2 (메모리)"]
  python demo_ai.py why    --status <폴더> --release <폴더> --mentor <폴더> --site 평택 --family "C4 (메모리)" [--line P1D]
  python demo_ai.py ping                             # 연결 테스트 한 줄
"""
import glob, json, os, re, sys
from collections import Counter
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svhm.ai.llm_client import LLMClient
from svhm.ai.query_parser import parse_query, to_pretty
from svhm.ai.column_mapper import ask_profile, apply_status_profile, transpose_horizontal_release
from svhm.ai.report_writer import write_report, report_to_markdown

args = sys.argv[1:]
cmd = args[0] if args else "help"
def opt(name, default=None):
    if name in args:
        i = args.index(name); v = args[i + 1]; del args[i:i + 2]; return v
    return default
client = LLMClient()
print("설정:", client.s.describe())

if cmd == "ping":
    print(client.complete_text("한국어로 한 문장만 답해.", "연결 테스트입니다. '연결됨' 이라고만 답하세요."))

elif cmd == "query":
    qc = parse_query(args[1], client)
    print(to_pretty(qc))
    if qc.need_clarification:
        print("→ 되묻기:", qc.clarification_question)

elif cmd == "map":
    from svhm.excel.status_parser import parse_status_rows, rows_from_openpyxl
    from svhm.excel.release_parser import parse_release_rows
    path, sheet = args[1], (args[2] if len(args) > 2 else None)
    rows = rows_from_openpyxl(path, sheet)
    st = parse_status_rows(rows, sheet or "sheet")
    rl = parse_release_rows(rows, sheet or "sheet")
    if not st.needs_ai_mapping and len(st.rows) >= 3:
        print("규칙 파서(현황표)가 이미 읽음:", st.summary()); sys.exit()
    if not rl.needs_ai_mapping and len(rl.versions) >= 2:
        print("규칙 파서(릴리즈노트)가 이미 읽음:", rl.summary()); sys.exit()
    print("규칙 파서: 현황표로도 릴리즈노트로도 못 읽음 →", st.summary())
    prof = ask_profile(rows, client)
    print("AI 프로파일:", json.dumps(prof.model_dump(), ensure_ascii=False))
    if prof.doc_type == "RELEASE" and prof.orientation == "horizontal":
        res = parse_release_rows(transpose_horizontal_release(rows, prof.header_row), sheet or "sheet")
        print("전치 후 릴리즈노트 파서:", res.summary())
    elif prof.doc_type == "STATUS" and prof.header_row:
        res = parse_status_rows(apply_status_profile(rows, prof), sheet or "sheet")
        print("프로파일 적용 후 현황표 파서:", res.summary())
    else:
        print("AI 도 구조를 못 정함 → 사용자가 열을 직접 지정하는 화면으로")

elif cmd in ("report", "why"):
    from svhm.core import refdata as R
    from svhm.core.pipeline import build_registry, match_equipment, summarize
    from svhm.core.risk import RiskRule, assess_all, priority_list
    from svhm.core.forecast import SnapshotPoint, forecast
    from svhm.excel.status_parser import parse_workbook
    status_dir, rel, mentor = opt("--status", "."), opt("--release", "."), opt("--mentor", ".")
    site, fam_filter, line_filter = opt("--site", "평택"), opt("--family"), opt("--line")
    F = lambda d, n: os.path.join(d, n)
    MAP = {1: (F(rel, "P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx"), "구UI"), 2: (F(rel, "P_PJT_M-C3_SW_정기_Release_List_교육용.xlsx"), "통합UI"),
           4: (F(rel, "P_PJT_M-C3_Foundry_SW_Release_List_교육용.xlsx"), "통합UI"), 5: (F(mentor, "P_PJT_M-C4_SW_정기_Release_List_교육용.xlsx"), None),
           6: (F(rel, "P_PJT_M-C4_Foundry_SW_Release_List_교육용.xlsx"), None), 7: (F(rel, "P_PJT_M-O2_SW_정기_Release_List_교육용.xlsx"), "Memory"),
           8: (F(rel, "P_PJT_M-O2_Foundry_SW_Release_List_교육용.xlsx"), "Foundry"), 9: (F(rel, "P_PJT_M-O3_SW_정기_Release_List_교육용.xlsx"), None),
           10: (F(mentor, "P_PJT_M-O5_SW_정기_Release_List_교육용.xlsx"), None), 11: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Inline Ox"),
           12: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Inline O2"), 13: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Cluster Ox"),
           14: (F(rel, "P_PJT_KCTC_SW_정기_Release_List_교육용.xlsx"), "Cluster O2")}
    reg = build_registry({k: v for k, v in MAP.items() if os.path.exists(v[0])})
    n_by = {(fid, var): sorted(lad.by_variant(var), key=lambda r: r.lineage_order)[-1].name for fid, lad in reg.ladders.items() for var in {r.variant for r in lad.rungs}}
    files = sorted(set(glob.glob(os.path.join(status_dir, f"*{site}*_20??-??-??.xlsx"))))
    snaps = {}
    for f in files:
        d = date.fromisoformat(re.search(r"(\d{4}-\d{2}-\d{2})", f).group(1))
        snaps[d] = [match_equipment(r, reg) for s in parse_workbook(f)["results"] for r in s.rows]
    latest_d = max(snaps); latest = snaps[latest_d]
    def select(ms):
        out = [m for m in ms if any((fam_filter is None or v.family_name == fam_filter) for v in m.versions)]
        return [m for m in out if line_filter is None or m.line == line_filter]
    sel = select(latest)
    s = summarize(sel, reg, n_by)
    rules = [RiskRule(1, "SAFETY_REQUIRED", 5, "v4.56.6.43", None, "환경안전 필수", "GAS MIX Interlock 상태 표시"),
             RiskRule(2, "SAFETY_REQUIRED", 7, "v3.52_12", None, "환경안전 필수", "O2 환경안전 항목(데모)")]
    risks = assess_all(sel, reg, rules, latest_d)
    pr = priority_list(risks, reg, n_by)
    pts = [SnapshotPoint(d, summarize(select(ms), reg, n_by).applied, summarize(select(ms), reg, n_by).total) for d, ms in sorted(snaps.items())]
    fc = forecast(pts)
    scope = f"{site}" + (f" / {line_filter}" if line_filter else "") + (f" / {fam_filter}" if fam_filter else " / 전체")
    data = {"title": f"{scope} SW 버전 적용 현황 ({latest_d})", "scope": scope, "base_date": str(latest_d),
            "summary": {"total": s.total, "applied": s.applied, "need_upgrade": s.need_upgrade, "unregistered": s.unregistered, "missing": s.missing, "excluded": s.excluded, "rate": s.rate()},
            "risk": {"count": sum(1 for r in risks if r.is_risk), "rules": [f"{r.rule_type} {R.FAMILIES[r.family_id]['name']} {r.start_version}: {r.item_ref}" for r in rules]},
            "priority_top": [{"rank": rk, "equipment": er.match.equipment_code, "line": er.match.line, "group": er.match.group_name, "why": why[:120]} for rk, er, why in pr[:5]],
            "forecast": {"message": fc.message, "points": fc.points, "status": fc.status},
            "snapshots": [{"date": str(p.base_date), "applied": p.applied, "total": p.total} for p in pts]}
    if cmd == "why":
        by_ver = Counter(v.result.rung.name for m in sel for v in m.versions if v.result.rung)
        lad_items = {}
        for fid, (pth, sh) in MAP.items():
            if R.FAMILIES[fid]["name"] == fam_filter and os.path.exists(pth):
                from svhm.excel.release_parser import parse_release_workbook
                rr = parse_release_workbook(pth, [sh] if sh else None)[0]
                for v in rr.versions:
                    its = [i for i in rr.items if i.source_row in v.item_rows]
                    lad_items[v.name] = {"release_date": str(v.release_date), "items": len(its),
                                         "hw_change_items": sum(1 for i in its if i.extras.get("hw") in ("O", "Y", "예")),
                                         "customer_items": sum(1 for i in its if i.extras.get("customer") in ("O", "Y", "예")),
                                         "hotfix": bool(re.search(r"_[a-z]\d*$", v.name)), "safety_items": sum(1 for i in its if i.is_safety_candidate)}
        data["versions_in_scope"] = [{"version": k, "count": c, **lad_items.get(k, {})} for k, c in by_ver.most_common()]
        data["target_version"] = n_by.get(next(((v.family_id, v.result.rung.variant) for m in sel for v in m.versions if v.result.rung), (None, "")))
    rep = write_report(data, client, kind=cmd)
    print(report_to_markdown(rep))
    os.makedirs("parsed_ai", exist_ok=True)
    with open(f"parsed_ai/{cmd}_{site}_{latest_d}.md", "w", encoding="utf-8") as f:
        f.write(report_to_markdown(rep))
    with open(f"parsed_ai/{cmd}_{site}_{latest_d}_input.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    print(f"저장: parsed_ai/{cmd}_{site}_{latest_d}.md (+ _input.json: AI 에 준 숫자)")
else:
    print(__doc__)
