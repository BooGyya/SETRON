"""원본 catalog를 보존하며 시연용 릴리즈·HPA SW를 메모리에서만 보완한다.

원본 현황에 있던 버전 원문은 별칭으로 합치지 않는다. 새 릴리즈의 출시일·순서·Item·
모델 적용 근거는 실제 제품 정보가 아닌 합성 시나리오이며 별도 입력 작업에 기록한다.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import re

from seed_source import make_plan

AUGMENTATION_REVISION = 4
REGISTERED_LINES_KEY = "registered_lines.v1"
SYNTHETIC_NOTICE = "합성 시연 데이터: 실제 제품의 출시일·비교순서·변경사항·모델 적용·라인 배치 근거가 아님"
HPA_VERSION = "v1.0.0"
RELATED_FAMILIES = {"c3_legacy_foundry": "c3_legacy", "c4_foundry": "c4",
                    "inline_o2": "inline_ox", "cluster_o2": "cluster_ox"}


def snapshot_template(catalog, entry):
    """원본 template와 이번 시연에서 명시한 현황 필드 변경을 합쳐 반환한다."""
    return {**catalog["templates"][entry["template"]], **entry.get("snapshot_overrides", {})}


def _cover_source_lines(catalog, plan):
    """모델·계열·사업장·생산 구분과 SW 이력을 유지하며 누락된 원본 라인도 최소 한 대 선택한다."""
    templates = catalog["templates"]
    counts = Counter((templates[entry["template"]]["site"], templates[entry["template"]]["line"]) for entry in plan)
    missing = {(template["site"], template["line"]) for template in templates} - set(counts)
    replacements = []
    for target in sorted(missing):
        replacement = None
        for template_id, template in enumerate(templates):
            if (template["site"], template["line"]) != target:
                continue
            for entry in plan:
                old = templates[entry["template"]]
                donor = old["site"], old["line"]
                if (old["model"], old["family"], old["site"], old["area"]) == (
                        template["model"], template["family"], template["site"], template["area"]) and counts[donor] > 1:
                    replacement = entry, template_id, donor
                    break
            if replacement:
                break
        if replacement is None:
            raise ValueError(f"{target}: 모델·계열별 대수와 기존 라인을 유지하며 누락 라인을 포함할 수 없습니다.")
        entry, template_id, donor = replacement
        replacements.append({"equipment_code": entry["code"], "original_template": entry["template"],
                             "replacement_template": template_id, "original_site": donor[0], "original_line": donor[1],
                             "selected_site": target[0], "selected_line": target[1],
                             "notice": "[합성 시연] 원본 현황 template 선택 보완. 기존 6개월 SW 관측은 유지"})
        counts[donor] -= 1
        counts[target] += 1
        entry["template"] = template_id
    return replacements


def _split_p4_lines(catalog, plan, book_id, sheets):
    """원본 P4F 19대의 현황 Line만 승인된 P4H 10대·P4D 9대 시연 라인으로 나눈다."""
    selected = [entry for entry in plan if (snapshot_template(catalog, entry)["site"],
                snapshot_template(catalog, entry)["line"]) == ("평택", "P4F")]
    if len(selected) != 19 or any(snapshot_template(catalog, entry)["area"] != "MEMORY" for entry in selected):
        raise ValueError("P4F의 평택 Memory 19대가 승인한 시연 라인 분할 조건과 다릅니다.")
    sheet = "합성_라인_시나리오"
    cells = sheets[sheet] = {"A1": "설비 호기", "B1": "원본 사업장", "C1": "원본 라인",
        "D1": "시연 사업장", "E1": "시연 라인", "F1": "생산 구분", "G1": "출처 구분", "H1": "원본 template 호기"}
    for number, entry in enumerate(selected):
        original = catalog["templates"][entry["template"]]
        line = "P4H" if number < 10 else "P4D"
        row = number + 2
        for column, value in zip("ABCDEFGH", (entry["code"], original["site"], original["line"], "평택", line,
                "MEMORY", "[합성] 원본 P4F를 P4H/P4D로 나눈 테스트 라인. 실제 사업장 라인 배치가 아님", original["source_equipment"])):
            cells[f"{column}{row}"] = value
        entry["snapshot_overrides"] = {"line": line, "area": "MEMORY"}
        entry["snapshot_sources"] = {"line": {"workbook": book_id, "sheet": sheet, "cells": [f"E{row}"]}}
    return {"notice": "[합성] P4H/P4D는 원본 P4F의 시연용 대체 라인", "source_site": "평택", "source_line": "P4F",
            "production_area": "MEMORY", "equipment_counts": {"P4H": 10, "P4D": 9}, "synthetic_workbook": book_id, "sheet": sheet}


def prepare_seed(source_catalog):
    """원본은 보존하고, 승인한 300대·6개월 구성에 사례 형식의 추정값을 보완한다."""
    from seed_equipment_names import assign_equipment_codes, original_equipment_conflicts
    from seed_scenarios import apply_scenarios, prepare_scenario_templates
    from setron.features.item.jira import extract_jira_from_source

    catalog = deepcopy(source_catalog)
    source_item_jira = {}
    for key, family in source_catalog["families"].items():
        raw = source_catalog["workbooks"][family["workbook"]]["sheets"]
        cells = raw[family["sheet"]]
        headers = {address: value for address, value in cells.items()
                   if isinstance(value, str) and value.strip().casefold() in {"jira", "remark"}}
        jira_columns = {re.match(r"[A-Z]+", address)[0] for address in headers}
        for index, item in enumerate(family["items"]):
            row = re.search(r"\d+$", item["cell"])[0]
            # 실제 주소·원문을 유지한 필요한 셀만 전달해 매 Item마다 전체 시트를 다시 훑지 않는다.
            addresses = {item["cell"], *headers, *(column + row for column in jira_columns)}
            nearby = {family["sheet"]: {address: cells[address] for address in addresses if address in cells}}
            evidence = extract_jira_from_source(nearby, {"content": {"sheet": family["sheet"], "cells": [item["cell"]]}},
                                                content=item["content"])
            if evidence.jira_key is not None:
                source_item_jira.setdefault(key, {})[str(index)] = {
                    "jira_key": evidence.jira_key, "source_reference": evidence.source_reference}
    plan = make_plan(source_catalog)
    coverage_replacements = _cover_source_lines(catalog, plan)
    scenario_templates = prepare_scenario_templates(source_catalog, catalog, plan)
    previous_codes = [entry["code"] for entry in plan]
    equipment_assignments = assign_equipment_codes(catalog, plan)
    renamed = dict(zip(previous_codes, (entry["code"] for entry in plan), strict=True))
    for replacement in coverage_replacements:
        replacement["equipment_code"] = renamed[replacement["equipment_code"]]
    hpa_area_evidence = []
    for entry in plan:
        template = catalog["templates"][entry["template"]]
        if template["model"] != "HPA":
            continue
        reason = catalog["workbooks"][template["workbook"]]["sheets"]["S5"]["Z20"]
        entry.setdefault("snapshot_overrides", {})["area"] = None
        entry.setdefault("snapshot_sources", {})["production_area"] = {
            "workbook": template["workbook"], "sheet": "S5", "cells": ["Z20"]}
        hpa_area_evidence.append({"equipment_code": entry["code"], "production_area": None,
            "classification": "NOT_APPLICABLE", "workbook": template["workbook"],
            "sheet": "S5", "cell": "Z20", "reason": reason})
    book_id = len(catalog["workbooks"])
    sheets = {}
    book = {"name": "SETRON 사례 형식 보완 자료 (추정 데이터)", "sheets": sheets,
            "synthetic": True, "notice": SYNTHETIC_NOTICE}
    catalog["workbooks"].append(book)
    inferred_releases, inferred_items, history_adjustments = [], [], []
    content_use = Counter()
    used_contents = {key: {item["content"] for item in family["items"]}
                     for key, family in source_catalog["families"].items()}

    def related_basis(key, raw):
        """같은 원문의 알려진 날짜를 우선하며 표기·분기 추정은 별도로 기록한다."""
        related_key = key if key in source_catalog["families"] else RELATED_FAMILIES.get(key)
        related = source_catalog["families"].get(related_key, {})
        versions = related.get("versions", [])
        matches = [v for v in versions if v["name"].strip().casefold() == raw.strip().casefold()]
        kind = "동일 원문 또는 대소문자·끝 공백 표기의 관련 원본 출시일"
        if not matches and key == "c4_foundry":
            stem = raw.split("_")[0].casefold()
            matches = [v for v in versions if v["name"].split("_")[0].casefold() == stem]
            kind = "동일 숫자 본체의 관련 C4 분기 출시일 참고; 파운드리 적용은 추정"
        if not matches and " + Hotfix" in raw:
            base = raw.split(" + Hotfix", 1)[0]
            matches = [v for v in versions if v["name"] == base]
            kind = "관련 기본 버전의 후속 Hotfix 날짜 추정"
        if not matches and key == "cluster_ox":
            matches = [v for v in versions if v["name"] == raw.replace("_", ".")]
            kind = "구분자가 다른 유사 버전의 날짜 참고; 별칭으로 확정하거나 원문을 합치지 않음"
        if matches:
            version = max(matches, key=lambda v: v["date"])
            day = version["date"]
            if "Hotfix" in kind:
                day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            return day, {"kind": kind, "family": related_key, "version": version["name"],
                         "workbook": related["workbook"], "sheet": related["sheet"],
                         "cell": version["cell"], "date_cell": version["date_cell"]}
        if key == "c4" and raw == "v4.56.5.9":
            first = min(versions, key=lambda v: v["date"])
            return (date.fromisoformat(first["date"]) - timedelta(days=7)).isoformat(), {
                "kind": "이전 숫자 본체의 선행 버전 날짜 추정", "family": key, "version": first["name"],
                "workbook": related["workbook"], "sheet": related["sheet"], "cell": first["cell"]}
        return None, {"kind": "원본 릴리즈에 없는 원문; 최초 관측일을 출시일 상한으로 사용", "family": related_key}

    def natural_content(key, models):
        """관련 원문의 기능·문체를 참고하되 원본 Item 자체는 수정하지 않는다."""
        related_key = key if key in source_catalog["families"] else RELATED_FAMILIES.get(key, "o5")
        related = source_catalog["families"][related_key]
        pool = [item for item in related["items"] if set(item["models"]) & set(models)] or related["items"]
        used = used_contents.setdefault(key, set())
        for attempt in range(len(pool)):
            donor = pool[content_use[key] % len(pool)]
            content_use[key] += 1
            content = donor["content"]
            # 참고 원문의 Ticket은 새 변경사항의 실제 Jira 근거로 복제하지 않는다.
            content = re.sub(r"\s*\(\s*ETOJ[- ]?\d{4}\s*\)", "", content, flags=re.IGNORECASE)
            content = re.sub(r"\bETOJ[- ]?\d{4}\b", "", content, flags=re.IGNORECASE)
            for boilerplate in (" — 예외 케이스 처리 보강", " 운영 로그 및 이력 관리 항목 보강", " (설비 초기 기동 시)"):
                content = content.replace(boilerplate, "")
            content = content.replace("로직 로직", "로직").replace("세분화 Alarm 발생 조건 및 Clear 조건 세분화", "세분화")
            for old, new in (("검증 기능 추가", "검증 조건 보완"), ("기능 추가", "기능 개선"),
                             ("로직 개선", "로직 보완"), ("기능 개선", "동작 개선"),
                             ("처리 보강", "처리 조건 보완"), ("현상 수정", "오류 및 복구 처리 보완"),
                             ("조건 세분화", "조건 보완"), ("개선", "보완"), ("추가", "보완")):
                if old in content:
                    content = content.replace(old, new, 1)
                    break
            else:
                content = content.rstrip() + " 적용 조건 보완"
            if content not in used:
                used.add(content)
                break
        else:
            raise ValueError(f"{key}: 중복되지 않는 추정 Item 문구를 선택할 수 없습니다.")
        basis = {"kind": "관련 원문 기능·표현을 참고한 추정 변경사항", "family": related_key,
                 "workbook": related["workbook"], "sheet": related["sheet"], "cell": donor["cell"],
                 "model_applicability_inferred": True}
        if key == "hpa":
            basis["limitation"] = "HPA 릴리즈 원본이 없어 공통 제어 기능의 문체만 참고; 실제 HPA 기능·버전 근거 없음"
        return content, basis

    def record(key, raw, day, order, content, models, *, origin=None, basis=None):
        sheet_name = "합성_" + key
        cells = sheets.setdefault(sheet_name, {
            "A1": "버전 원문", "B1": "출시일 (합성)", "C1": "비교순서 (합성)",
            "D1": "Item 번호 (합성)", "E1": "변경 내용 (합성)", "F1": "적용 모델 (합성)",
            "G1": "출처 구분", "H1": "원문 위치", "I1": "참고 내용의 근거"})
        row = 1 + sum(address.startswith("A") for address in cells)
        for column, value in zip("ABCDEFGHI", (raw, day, order, row - 1, content,
                ", ".join(models), SYNTHETIC_NOTICE, origin or "사례 형식으로 추정한 버전",
                json.dumps(basis or {}, ensure_ascii=False, sort_keys=True))):
            cells[f"{column}{row}"] = value
        return {"workbook": book_id, "sheet": sheet_name, "cell": f"A{row}",
                "date_cell": f"B{row}", "order_cell": f"C{row}", "content_cell": f"E{row}",
                "model_cell": f"F{row}", "item_no": row - 1, "synthetic": True}

    def add_item(key, version, models, *, origin=None):
        family = catalog["families"][key]
        content, basis = natural_content(key, models)
        location = record(key, version["name"], version["date"], version["order"], content, models, origin=origin, basis=basis)
        item = {"no": location["item_no"], "content": content, "cell": location["content_cell"],
                "models": models, "marks": [], "workbook": book_id, "sheet": location["sheet"],
                "synthetic": True, "model_cell": location["model_cell"], "inference": basis}
        version["items"].append(len(family["items"]))
        family["items"].append(item)
        inferred_items.append({"family": key, "version": version["name"], "models": models,
                               "content": content, "basis": basis})
        return location

    def add_release(key, raw, day, models, *, origin=None, basis=None):
        family = catalog["families"].setdefault(key, {"workbook": book_id, "sheet": "합성_" + key,
                                                       "versions": [], "items": [], "synthetic": True})
        used_orders = {v["order"] for v in family["versions"]}
        earlier = [v["order"] for v in family["versions"] if v["date"] <= day]
        order = max(earlier, default=min(used_orders, default=10) - 10) + 1
        while order in used_orders:
            order += 1
        later = [v["order"] for v in family["versions"] if v["date"] > day]
        if later and order >= min(later):
            raise ValueError(f"{key}: 원본 비교순서 사이에 합성 버전을 배치할 여유가 없습니다.")
        version = {"name": raw, "date": day, "order": order, "variant": "", "items": [],
                   "synthetic": True, "workbook": book_id, "sheet": "합성_" + key, "inference": basis or {}}
        family["versions"].append(version)
        location = add_item(key, version, models, origin=origin)
        version.update({field: location[field] for field in ("cell", "date_cell", "order_cell")})
        inferred_releases.append({"family": key, "version": raw, "released_at": day, "basis": basis or {}})
        return len(family["versions"]) - 1

    # 원본 없는 네 계열의 후보도 관련 원본과 같은 표기·달력 순서를 사용한다.
    missing = {t["family"] for t in catalog["templates"] if t["model"] != "HPA"} - set(catalog["families"])
    for key in sorted(missing):
        models = sorted({t["model"] for t in catalog["templates"] if t["family"] == key})
        if key == "c4_foundry":
            choices = [("v4.56.6.40_f1", "2026-01-02"), ("v4.56.6.41_f0", "2026-01-05"),
                       ("v4.56.6.41_f0a", "2026-01-07")]
        else:
            related = source_catalog["families"][RELATED_FAMILIES[key]]
            chosen = related["versions"][:3] if key == "c3_legacy_foundry" else [
                v for v in related["versions"] if v["date"] <= "2026-05-21"][-3:]
            choices = [(v["name"], v["date"]) for v in chosen]
        for raw, day in choices:
            _, basis = related_basis(key, raw)
            if key == "c4_foundry":
                basis = {"kind": "현황의 C4 파운드리 _f 분기 표기를 참고한 선행 버전·날짜 추정", "family": "c4"}
            add_release(key, raw, day, models, basis=basis)

    # 같은 원문은 같은 계열의 별도 릴리즈에 정확히 연결한다. 공백·대소문자 별칭을 합치지 않는다.
    missing_observations = defaultdict(list)
    for entry in plan:
        template = catalog["templates"][entry["template"]]
        for observation in entry["observations"]:
            if observation["release"] is None:
                if not observation["version"] and template["model"] != "HPA":
                    raise ValueError("HPA 외 설비의 버전 원문 공란은 합성 HPA 버전으로 채울 수 없습니다.")
                raw = observation["version"] or HPA_VERSION
                missing_observations[template["family"], raw].append((template, observation))
    for (key, raw), observations in sorted(missing_observations.items()):
        first_observed = min(observation["date"] for _, observation in observations)
        known_day, basis = related_basis(key, raw)
        day = known_day or first_observed
        if key == "hpa":
            basis = {"kind": "HPA 릴리즈 원본 없음; 필수 SW 값의 일반적인 숫자 표기와 최초 관측일을 추정", "family": None}
        models = sorted({template["model"] for template, _ in observations})
        template, first = observations[0]
        origin = None if not first["version"] else (
            catalog["workbooks"][first["workbook"]]["name"] + " / " + first["sheet"] + " / " + first["cell"])
        index = add_release(key, raw, day, models, origin=origin, basis=basis)
        for _, observation in observations:
            observation["release"] = index
            if not observation["version"]:
                version = catalog["families"][key]["versions"][index]
                observation.update(version=raw, workbook=book_id, sheet=version["sheet"], cell=version["cell"])

    # 원본 없는 계열의 추정 순서는 관련 원문 출시일을 반영한다. 원본 계열의 순서는 그대로 둔다.
    for key in missing:
        for order, version in enumerate(sorted(catalog["families"][key]["versions"], key=lambda v: (v["date"], v["name"])), 1):
            version["order"] = order * 10
            sheets[version["sheet"]][version["order_cell"]] = version["order"]

    # 관련 원본의 알려진 출시일보다 이른 월에는 앞선 후보를 사용한다. 원본 셀과 출시 이후 원문은 보존한다.
    for entry in plan:
        template = catalog["templates"][entry["template"]]
        family = catalog["families"][template["family"]]
        for observation in entry["observations"]:
            version = family["versions"][observation["release"]]
            if version["date"] <= observation["date"]:
                continue
            available = [(i, v) for i, v in enumerate(family["versions"]) if v["date"] <= observation["date"]
                         and any(template["model"] in family["items"][n]["models"] for n in v["items"])]
            if not available:
                raise ValueError(f"{entry['code']}: 출시 전 월의 이전 버전 후보가 없습니다.")
            index, earlier = max(available, key=lambda choice: choice[1]["order"])
            history_adjustments.append({"equipment_code": entry["code"], "family": template["family"],
                "base_date": observation["date"], "previous_version": observation["version"],
                "version": earlier["name"], "known_release_date": version["date"],
                "reason": "관련 원본의 알려진 출시 전 월 관측을 앞선 추정 후보로 재구성"})
            observation.update(version=earlier["name"], release=index, workbook=earlier.get("workbook", family["workbook"]),
                               sheet=earlier.get("sheet", family["sheet"]), cell=earlier["cell"])

    # Prime 등 기존 릴리즈의 실제 모델 적용 근거가 없는 경우에도 합성 근거를 별도 Item으로 남긴다.
    for entry in plan:
        template = catalog["templates"][entry["template"]]
        if template["model"] == "HPA":
            continue
        key, model = template["family"], template["model"]
        for observation in entry["observations"]:
            index = observation["release"]
            family = catalog["families"][key]
            version = family["versions"][index]
            if not any(model in family["items"][n]["models"] for n in version["items"]):
                add_item(key, version, [model], origin="실제 릴리즈의 버전 원문·출시일·순서는 원본 보존; 모델 적용 근거와 Item만 합성")

    scenarios = apply_scenarios(source_catalog, catalog, plan)
    line_scenario = _split_p4_lines(catalog, plan, book_id, sheets)
    values = [snapshot_template(catalog, entry) for entry in plan]
    lines = sorted({(value["site"], value["line"], value["area"]) for value in values if value["area"] is not None})
    if any(area not in {"MEMORY", "FOUNDRY"} for _, _, area in lines):
        raise ValueError("시연 라인의 생산 구분이 확인되지 않았습니다.")
    if {(value["site"], value["line"]) for value in values} != {(site, line) for site, line, area in lines}:
        raise ValueError("비적용 설비 외에 라인의 생산 구분 근거가 필요합니다.")
    registered_lines = [{"site": site, "line": line, "fab": area} for site, line, area in lines]
    book["sha256"] = hashlib.sha256(json.dumps(sheets, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
    catalog["seed_augmentation"] = {"revision": AUGMENTATION_REVISION, "notice": SYNTHETIC_NOTICE,
                                    "synthetic_workbook": book_id, "line_scenarios": [line_scenario],
                                    "source_line_coverage_replacements": coverage_replacements,
                                    "registered_lines": registered_lines, "equipment_assignments": equipment_assignments,
                                    "original_equipment_conflicts": original_equipment_conflicts(source_catalog),
                                    "inferred_releases": inferred_releases, "inferred_items": inferred_items,
                                    "history_adjustments": history_adjustments, "scenario_templates": scenario_templates,
                                    "scenarios": scenarios, "hpa_production_area": hpa_area_evidence}
    catalog["seed_augmentation"]["source_item_jira"] = source_item_jira
    catalog["seed_augmentation"]["jira_coverage"] = {
        "source_items": sum(len(family["items"]) for family in source_catalog["families"].values()),
        "with_jira": sum(len(values) for values in source_item_jira.values()),
        "unique_jira": len({item["jira_key"] for values in source_item_jira.values() for item in values.values()}),
        "synthetic_item_policy": "추정 Item에는 원본 Ticket을 복제하지 않으며 jira_code=NULL로 저장"}
    return catalog, plan
