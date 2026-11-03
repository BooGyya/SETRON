# -*- coding: utf-8 -*-
"""현황표 파서 데모: 엑셀을 읽어 (1) 사람이 읽는 요약을 화면에, (2) 검토용 CSV(한글 제목), (3) 개발용 JSON을 저장.
    python demo.py <현황표.xlsx> [...] [--out 결과폴더]
"""
import csv, json, os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svhm.excel.status_parser import parse_workbook, issue_text, ISSUE_LABELS

args = sys.argv[1:]
out_dir = "parsed"
if "--out" in args:
    i = args.index("--out"); out_dir = args[i + 1]; args = args[:i] + args[i + 2:]
os.makedirs(out_dir, exist_ok=True)

HEADERS = ["사이트", "시트", "라인", "층", "표(공정)", "메모리/파운드리", "설비군(원문)", "설비호기", "설비호기 꼬리말",
           "버전(원문)", "버전(분리)", "확인 필요 사유", "이슈코드", "원본 위치", "기타 칸"]

for path in args:
    res = parse_workbook(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    rows, review_lines, counter = [], [], Counter()
    print(f"\n[{os.path.basename(path)}]  사업장: {res['site'] or '모름'}")
    for sr in res["results"]:
        print("■", sr.summary())
        for w in sr.human_warnings():
            print("   ⚠", w)
        for r in sr.rows:
            counter.update(r.issues)
            rows.append({
                "사이트": r.site, "시트": r.sheet, "라인": r.line, "층": r.floor, "표(공정)": r.process_group,
                "메모리/파운드리": r.production_area, "설비군(원문)": r.group_raw, "설비호기": r.equipment_code,
                "설비호기 꼬리말": r.equipment_tail or "", "버전(원문)": r.version_raw,
                "버전(분리)": " | ".join(f"{v.label}:{v.text}" if v.label else v.text for v in r.versions),
                "확인 필요 사유": issue_text(r.issues), "이슈코드": ",".join(r.issues),
                "원본 위치": f"{r.source['sheet']} 시트 {r.source['row']}행",
                "기타 칸": json.dumps(r.extras, ensure_ascii=False) if r.extras else "",
            })
            if r.issues:
                review_lines.append(f"- {r.sheet} 시트 {r.source['row']}행 · {r.group_raw} · {r.equipment_code or '(설비호기 없음)'} · 버전 '{r.version_raw}' → {issue_text(r.issues)}")
    dup_codes = [c for c, _ in res["duplicates"]]
    print(f"   → 합계 설비 {len(rows)}대, 확인 필요 {sum(1 for r in rows if r['이슈코드'])}대, 중복 설비호기 {len(dup_codes)}건{(' (' + ', '.join(dup_codes) + ')') if dup_codes else ''}")
    if counter:
        print("   확인 필요 사유별:", ", ".join(f"{ISSUE_LABELS.get(k, k)} {v}대" for k, v in counter.most_common()))

    with open(f"{out_dir}/{stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS); w.writeheader(); w.writerows(rows)
    with open(f"{out_dir}/{stem}_검토요약.txt", "w", encoding="utf-8") as f:
        f.write(f"{os.path.basename(path)} 검토 요약\n설비 {len(rows)}대, 확인 필요 {len(review_lines)}대\n\n")
        f.write("\n".join(review_lines) if review_lines else "확인 필요 항목 없음")
        f.write("\n\n[시트별 경고]\n" + "\n".join(f"- {w}" for sr in res["results"] for w in sr.human_warnings()))
    with open(f"{out_dir}/{stem}.json", "w", encoding="utf-8") as f:
        json.dump({"site": res["site"], "duplicates": res["duplicates"],
                   "sheets": [{"sheet": s.sheet, "layout": s.layout, "warnings": s.warnings, "skipped": s.skipped,
                               "needs_ai_mapping": s.needs_ai_mapping, "blocks": [b.__dict__ for b in s.blocks],
                               "rows": [r.to_dict() for r in s.rows]} for s in res["results"]]}, f, ensure_ascii=False, indent=1)
    print(f"   저장: {out_dir}/{stem}.csv (검토표), {stem}_검토요약.txt, {stem}.json (개발용)")
