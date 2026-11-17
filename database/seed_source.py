"""예시 Excel을 읽어 원문·셀 위치와 재현 가능한 증강 계획을 만든다. 파일을 수정하지 않는다."""

from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
from pathlib import Path
import random
import re
import sys

from setron.features.reference_data import resolve_model_text, MODEL_STRUCTURES

DATES = tuple(date(2026, month, 21) for month in range(5, 11))
SEED_KEY = "demo_seed.v4:excel:300x6:2026-10-21"
LEGACY_KEY = "demo_seed.v1:300x6:2026-10-21"
GENERATOR_REVISION = 9   # 9: jira_code 저장으로 통일, 원본 Jira 987건·기존 셀 출처 보존
FAMILIES = (
    ("c3_legacy", "M-C3", "C3 구UI", "LEGACY"),
    ("c3", "M-C3", "C3 통합UI", "INTEGRATED"),
    ("c3_legacy_foundry", "M-C3", "C3 구UI (파운드리)", "LEGACY"),
    ("c3_foundry", "M-C3", "C3 통합UI (파운드리)", "INTEGRATED"),
    ("c4", "M-C4", "C4", None), ("c4_foundry", "M-C4", "C4 (파운드리)", None),
    ("o2", "M-O2", "O2", None), ("o2_foundry", "M-O2", "O2 (파운드리)", None),
    ("o3", "M-O3", "O3", None), ("o5", "M-O5", "O5", None),
    ("inline_ox", "Inline Ox", "Inline Ox", None), ("inline_o2", "Inline O2", "Inline O2", None),
    ("cluster_ox", "Cluster Ox", "Cluster Ox", None), ("cluster_o2", "Cluster O2", "Cluster O2", None),
    ("hpa", "HPA", "HPA", None),
)


def family_key(model, ui, area):
    if model == "M-C3":
        key = "c3_legacy" if ui == "LEGACY" else "c3"
    elif model in ("M-C4", "M-C4 DFB", "M-C4+"):
        key = "c4"
    elif model == "M-O2":
        key = "o2"
    else:
        key = {"M-O3": "o3", "XMO3": "o3", "M-O5": "o5", "M-O5 Prime": "o5",
               "Inline Ox": "inline_ox", "Inline O2": "inline_o2", "Cluster Ox": "cluster_ox",
               "Cluster O2": "cluster_o2", "HPA": "hpa"}[model]
    return key + "_foundry" if area == "FOUNDRY" and key in ("c3", "c3_legacy", "c4", "o2") else key


def read_sources(folder):
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter as col
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai"))
    from svhm.excel.release_parser import parse_release_rows
    from svhm.excel.status_parser import parse_status_rows

    result = {"schema": 2, "description": "예시 설비 현황·Item 원문 기반 증강. 6개월 관측과 DEMO 호기는 합성.",
              "workbooks": [], "families": {}, "templates": [], "excluded": []}
    opened = {}
    for path in sorted(folder.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        book = load_workbook(path, read_only=True, data_only=True)
        sheets = {sheet.title: [list(row) for row in sheet.iter_rows(values_only=True)] for sheet in book}
        book.close()
        # 문자열 원문(공백·줄바꿈 포함)을 별도로 보관한다.
        raw = {name: {f"{col(c+1)}{r+1}": value.isoformat() if isinstance(value, (date, datetime)) else value
                      for r, row in enumerate(rows) for c, value in enumerate(row) if value is not None}
               for name, rows in sheets.items()}
        index = len(result["workbooks"])
        result["workbooks"].append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "sheets": raw})
        opened[path.name] = index, sheets

    specs = (("M-C3", "구UI", "c3_legacy"), ("M-C3", "통합UI", "c3"), ("M-C3", "Foundry", "c3_foundry"),
             ("M-C4", "Release List", "c4"), ("M-O2", "Memory", "o2"), ("M-O2", "Foundry", "o2_foundry"),
             ("M-O3", "Release", "o3"), ("M-O5", "Release List", "o5"),
             ("KCTC", "Inline", "inline_ox"), ("KCTC", "Cluster", "cluster_ox"))
    for tag, sheet, key in specs:
        index, sheets = opened[f"P_PJT_{tag}_SW_정기_Release_List_교육용.xlsx"]
        rows = sheets[sheet]
        versions, items = [], []
        if tag == "M-O2":
            for c, name in enumerate(rows[2]):
                if not c or not name:
                    continue
                day = str(rows[3][c]).replace(".", "-")
                links = []
                for r in range(5, len(rows)):
                    content = rows[r][c] if c < len(rows[r]) else None
                    if not content:
                        continue
                    links.append(len(items))
                    number = rows[r][0]
                    items.append({"no": int(number) if str(number).isdigit() else None, "content": str(content),
                                  "cell": f"{col(c+1)}{r+1}", "models": ["M-O2"], "marks": []})
                versions.append({"name": str(name).strip(), "date": day, "variant": "", "order": len(versions)*10+10,
                                 "cell": f"{col(c+1)}3", "date_cell": f"{col(c+1)}4", "items": links})
        else:
            parsed = parse_release_rows(rows, sheet)
            if parsed.needs_ai_mapping:
                raise ValueError(f"릴리즈 시트 형식을 확인해 주세요: {tag}/{sheet}")
            row_items = {}
            owner = next(f[1] for f in FAMILIES if f[0] == key)
            for item in parsed.items:
                r = item.source_row
                marks = []
                if tag == "M-C4":
                    for variant, c in parsed.flag_columns.items():
                        model = {"NORMAL": "M-C4", "DFB": "M-C4 DFB", "DEX": "M-C4+"}[variant]
                        marks.append({"model": model, "raw": rows[r-1][c-1], "cell": f"{col(c)}{r}",
                                      "header": rows[parsed.header_row-1][c-1], "header_cell": f"{col(c)}{parsed.header_row}"})
                models = [m["model"] for m in marks if str(m["raw"] or "").strip().upper() == "O"] if marks else [owner]
                if tag == "M-O5" and item.is_prime:
                    models = ["M-O5 Prime"]
                cell = f"{col(parsed.columns['content'])}{r}"
                row_items[r] = len(items)
                items.append({"no": item.no, "content": str(rows[r-1][parsed.columns['content']-1]),
                              "cell": cell, "models": models, "marks": marks})
            for v in parsed.versions:
                if not v.release_date or v.release_date > DATES[-1]:
                    continue
                versions.append({"name": v.name, "date": v.release_date.isoformat(), "variant": v.variant,
                                 "order": v.order, "cell": f"{col(parsed.columns['version'])}{v.source_row}",
                                 "date_cell": f"{col(parsed.columns.get('date', parsed.columns['version']))}{v.source_row}",
                                 "items": [row_items[r] for r in v.item_rows]})
        result["families"][key] = {"workbook": index, "sheet": sheet, "versions": versions, "items": items}

    for name, (index, sheets) in opened.items():
        if "설비현황표" not in name:
            continue
        site = "평택" if name.startswith("평택") else "화성"
        for sheet, rows in sheets.items():
            parsed = parse_status_rows(rows, sheet, site)
            for row in parsed.rows:
                resolved = resolve_model_text(row.group_raw, row.version_raw)
                if not resolved or (not row.versions and (resolved or [None])[0] != "HPA"):
                    result["excluded"].append({"workbook": index, **row.source, "reason": "모델 또는 SW 연결 미확인"})
                    continue
                model, ui, modification, geometry = resolved
                if len(row.versions) > 1 or (row.version_raw and not re.match(r"^[vV]?\d", row.version_raw)):
                    result["excluded"].append({"workbook": index, **row.source, "reason": "복수 모델·설명문은 임의 SW 연결 없이 제외"})
                    continue
                r = row.source["row"]
                version_cell = f"{col(row.source['col_version'])}{r}"
                raw_version = result["workbooks"][index]["sheets"][sheet].get(version_cell)
                result["templates"].append({"workbook": index, "sheet": sheet, "source_equipment": row.equipment_code,
                    "equipment_cell": f"{col(row.source['col_equipment'])}{r}", "version_cell": version_cell,
                    "model": model, "model_raw": row.group_raw, "site": row.site, "line": row.line,
                    "area": row.production_area, "geometry": geometry, "modification": modification,
                    "structure": MODEL_STRUCTURES.get(model), "family": family_key(model, ui, row.production_area),
                    "version": str(raw_version) if raw_version not in (None, "") else None})
    return result


def lineage_for_model(model, key):
    """이 시드에서 확인한 C4 노트의 모델별 줄기 범위. 원본 표식을 변경하지 않는다."""
    return {"M-C4": "BASE", "M-C4 DFB": "DFB", "M-C4+": "DEX"}.get(model) if key == "c4" else None


def matches_lineage(model, key, version):
    expected = lineage_for_model(model, key)
    return expected is None or (version["variant"] or "BASE") == expected


def known_release_floor(catalog, key, raw):
    """표기 차이 후보는 자동 연결하지 않고 가장 늦은 알려진 출시일로만 소급 배치를 막는다."""
    if not isinstance(raw, str):
        return None
    dates = [date.fromisoformat(v['date']) for v in catalog['families'].get(key, {}).get('versions', [])
             if v['name'].strip().casefold() == raw.strip().casefold()]
    return max(dates) if dates else None


def candidates(catalog, model, key):
    family = catalog["families"].get(key, {})
    items = family.get("items", [])
    return [(i, row) for i, row in enumerate(family.get("versions", []))
            if any(model in items[n]["models"] for n in row["items"])
            and matches_lineage(model, key, row)]


def make_plan(catalog):
    """출처별 층화 추출과 설비별 독립 난수로 모델·버전의 고정 결합을 피한다."""
    rng = random.Random(20261104)
    grouped = defaultdict(list)
    hpa = []
    for template in catalog["templates"]:
        (hpa if template["model"] == "HPA" else grouped[(template["model"], template["family"])]).append(template)
    if not hpa or not grouped:
        raise ValueError("증강에 필요한 설비 현황이 없습니다.")
    allocation = {key: 5 for key in grouped}
    for _ in range(285 - sum(allocation.values())):
        key = min(grouped, key=lambda k: allocation[k] / len(grouped[k]))
        allocation[key] += 1
    selected = []
    for key in sorted(grouped):
        pool = grouped[key][:]
        rng.shuffle(pool)
        selected.extend(pool[i % len(pool)] for i in range(allocation[key]))
    selected += [hpa[i % len(hpa)] for i in range(15)]
    plan = []
    for number, template in enumerate(selected, 1):
        template_id = catalog["templates"].index(template)
        confirmed = {i for i, _ in candidates(catalog, template["model"], template["family"])}
        observed = {t['version'] for t in catalog['templates']
                    if (t['model'], t['family']) == (template['model'], template['family'])}
        eligible = [(i, v) for i, v in enumerate(catalog['families'].get(template['family'], {}).get('versions', []))
                    if (i in confirmed or v['name'] in observed)
                    and matches_lineage(template['model'], template['family'], v)]
        anchor = next((p for p, (_, v) in enumerate(eligible) if v["name"] == template["version"]), None)
        unregistered = anchor is None
        floor = known_release_floor(catalog, template['family'], template['version'])
        history_anchor = anchor
        if unregistered and floor is not None:
            # 원문은 출시일 이후에도 미등록으로 유지한다. 이전 달은 같은 모델의 알려진 과거 버전에서 생성한다.
            matches = [p for p, (_, v) in enumerate(eligible)
                       if v['name'].strip().casefold() == template['version'].strip().casefold()]
            earlier = [p for p, (_, v) in enumerate(eligible) if date.fromisoformat(v['date']) < floor]
            history_anchor = max(matches) if matches else max(earlier) if earlier else None
        # 미등록 원문을 다른 버전으로 치환하지 않는다. 등록 설비 일부의 최종 버전만 같은 모델 후보에서 분산한다.
        if anchor is not None and rng.random() < .35:
            anchor = rng.randrange(len(eligible))
            history_anchor = anchor
        lag = rng.randint(1, 8)
        previous = -1
        observations = []
        for month, day in enumerate(DATES):
            choice = None
            if history_anchor is not None and not (unregistered and floor is not None and day >= floor):
                ceiling = min(history_anchor, len(eligible)-1)
                desired = max(0, ceiling - round(lag * (5-month) / 5))
                available = [p for p in range(ceiling+1) if date.fromisoformat(eligible[p][1]["date"]) <= day]
                if available:
                    pos = min(max(previous, desired), max(available))
                    pos = max(p for p in available if p <= pos) if any(p <= pos for p in available) else min(available)
                    previous = pos
                    choice = eligible[pos]
            if choice:
                index, version = choice
                family = catalog["families"][template["family"]]
                observations.append({"date": day.isoformat(), "version": version["name"], "release": index,
                                     "workbook": family["workbook"], "sheet": family["sheet"], "cell": version["cell"]})
            else:
                raw = template['version'] if floor is None or day >= floor else None
                observations.append({"date": day.isoformat(), "version": raw, "release": None,
                                     "workbook": template["workbook"], "sheet": template["sheet"], "cell": template["version_cell"]})
        plan.append({"code": f"DEMO-{number:04}", "template": template_id, "observations": observations})
    return plan
