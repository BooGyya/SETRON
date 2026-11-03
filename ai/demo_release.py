# -*- coding: utf-8 -*-
"""릴리즈노트 파서 데모: 엑셀 → (1) 사람이 읽는 요약, (2) 버전 사다리 CSV, (3) 변경 항목 CSV, (4) 개발용 JSON
    python demo_release.py <릴리즈노트.xlsx> [...] [--out 결과폴더]
"""
import csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svhm.excel.release_parser import parse_release_workbook

args = sys.argv[1:]
out_dir = "parsed_release"
if "--out" in args:
    i = args.index("--out"); out_dir = args[i + 1]; args = args[:i] + args[i + 2:]
os.makedirs(out_dir, exist_ok=True)
APPLY_KO = {"ALL": "전체", "NORMAL": "일반", "DFB": "DFB", "DEX": "C4+", "UNKNOWN": "미확인(빈칸)", "NONE": "해당 없음"}

for path in args:
    stem = os.path.splitext(os.path.basename(path))[0]
    print(f"\n[{os.path.basename(path)}]")
    ladder_rows, item_rows, dump = [], [], []
    for r in parse_release_workbook(path):
        print("■", r.summary())
        for w in r.warnings[:5]:
            print("   ⚠", w)
        for v in r.versions:
            ladder_rows.append({"시트": r.sheet, "순서": v.order, "버전명": v.name, "기본 이름": v.base, "파생": v.variant or "기본",
                                "파생 안 순서": v.lineage_order, "릴리즈일": v.release_date.isoformat() if v.release_date else "",
                                "항목 수": len(v.item_rows), "비고": v.note, "원본 행": v.source_row})
        name_by_row = {}
        for v in r.versions:
            for row in v.item_rows:
                name_by_row.setdefault(row, []).append(v.name)
        for it in r.items:
            item_rows.append({"시트": r.sheet, "버전": " / ".join(name_by_row.get(it.source_row, [])), "No": it.no or "", "모듈": it.module,
                              "변경 내용": it.content, "JIRA": it.jira, "담당": it.owner, "우선순위": it.priority,
                              "적용 범위": ", ".join(APPLY_KO.get(a, a) for a in it.applies_to),
                              "위험 아이템 후보": "예" if it.is_safety_candidate else "", "Prime 전용": "예" if it.is_prime else "",
                              "핫픽스": {True: "Y", False: "N"}.get(it.hotfix_flag, ""), "원본 행": it.source_row,
                              "기타": json.dumps(it.extras, ensure_ascii=False) if it.extras else ""})
        dump.append({"sheet": r.sheet, "title": r.title, "header_row": r.header_row, "columns": r.columns, "flag_columns": r.flag_columns,
                     "needs_ai_mapping": r.needs_ai_mapping, "hint": r.hint, "warnings": r.warnings,
                     "versions": [v.to_dict() for v in r.versions], "items": [i.to_dict() for i in r.items]})
    for name, rows in (("사다리", ladder_rows), ("항목", item_rows)):
        if rows:
            with open(f"{out_dir}/{stem}_{name}.csv", "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    with open(f"{out_dir}/{stem}.json", "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)
    print(f"   저장: {out_dir}/{stem}_사다리.csv (버전 {len(ladder_rows)}개), {stem}_항목.csv (항목 {len(item_rows)}건), {stem}.json")
