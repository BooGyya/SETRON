"""원본 PM 사례와 승인된 미등록·노후·위험 검증 관측을 시드 사본에 추가한다.

원본 catalog는 읽기만 한다. 추정 버전·날짜·위험 지정은 별도 출처와 manifest에
기록하며, 실제 제품의 출시 이력이나 결함으로 확정하지 않는다.
"""
from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
import re

NOTICE = "승인된 판정 분기 검증 자료. 실제 출시일·제품 결함·위험을 뜻하지 않음."


def _template(catalog, entry):
    return {**catalog["templates"][entry["template"]], **entry.get("snapshot_overrides", {})}


def _location(workbook, sheet, *cells):
    return {"workbook": workbook, "sheet": sheet, "cells": list(cells)}


def _mixed_source(source):
    found = [(i, book["sheets"]["Foundry"]) for i, book in enumerate(source["workbooks"])
             if book["sheets"].get("Foundry", {}).get("D57") == "ESON81"]
    if len(found) != 1:
        raise ValueError("원본 Foundry D57의 ESON81 사례를 하나로 확인할 수 없습니다.")
    workbook, cells = found[0]
    match = re.fullmatch(r"O3:\s*(.*?)\s*/\s*O2:\s*(.*?)\s*", cells.get("G57", ""))
    if (cells.get("C57") != "M-O2/O3" or match is None
            or not re.fullmatch(r"PM1,\s*2는\s*O3,\s*PM3,\s*4는\s*O2", cells.get("P57", ""))):
        raise ValueError("ESON81의 원본 모델·SW·PM 배정이 승인한 사례와 다릅니다.")
    return workbook, cells, match.group(1), match.group(2)


def prepare_scenario_templates(source_catalog, catalog, plan):
    """호기 배정 전에 원본 혼합형을 같은 사업장·생산영역·O2 할당과 교체한다."""
    workbook, cells, o3_raw, o2_raw = _mixed_source(source_catalog)
    counts = Counter((_template(catalog, entry)["site"], _template(catalog, entry)["line"]) for entry in plan)
    donor = next((entry for entry in plan if (_template(catalog, entry)["site"],
                 _template(catalog, entry)["area"], _template(catalog, entry)["model"]) ==
                 ("화성", "FOUNDRY", "M-O2") and counts["화성", _template(catalog, entry)["line"]] > 1), None)
    if donor is None:
        raise ValueError("원본 라인을 유지하며 혼합형과 교체할 화성 Foundry O2 설비가 없습니다.")
    original = _template(catalog, donor)
    template = {**original, "workbook": workbook, "sheet": "Foundry", "source_equipment": "ESON81",
                "equipment_cell": "D57", "version_cell": "G57", "model": "M-O2", "model_raw": cells["C57"],
                "site": "화성", "line": cells["B57"], "area": "FOUNDRY", "family": "o2_foundry",
                "version": o2_raw, "geometry": None, "modification": None, "structure": None}
    catalog["templates"].append(template)
    donor["template"] = len(catalog["templates"]) - 1
    donor["scenario_key"] = "original_mixed_pm"
    donor["snapshot_overrides"] = {**donor.get("snapshot_overrides", {}), "group_model": None}
    donor["snapshot_sources"] = {
        "equipment_code": _location(workbook, "Foundry", "D57"),
        "model_name": _location(workbook, "Foundry", "C57"),
        "line": _location(workbook, "Foundry", "B57")}
    return {"kind": "ORIGINAL_MIXED_PM", "equipment_code": "ESON81", "template": donor["template"],
            "allocation_model": "M-O2", "donor_source_equipment": original["source_equipment"],
            "donor_line": original["line"], "source_line": cells["B57"], "source_area": "FOUNDRY",
            "source": _location(workbook, "Foundry", "B57", "C57", "D57", "G57", "P57"),
            "versions": {"o3": o3_raw, "o2_foundry": o2_raw},
            "counting": "물리 설비 1대, O2/O3 모델에 각각 1대. 기존 O2 할당 1대를 교체하여 모델별 합계는 1대 증가."}


def _release_observation(catalog, family_key, model, raw, day):
    family = catalog["families"][family_key]
    exact = [(i, version) for i, version in enumerate(family["versions"]) if version["name"] == raw]
    if len(exact) != 1:
        raise ValueError(f"{family_key}: 원본 혼합형 SW의 릴리즈를 확인할 수 없습니다.")
    index, target = exact[0]
    if target["date"] > day:
        available = [(i, version) for i, version in enumerate(family["versions"])
                     if version["date"] <= day and version["order"] <= target["order"]
                     and any(model in family["items"][n]["models"] for n in version["items"])]
        if not available:
            raise ValueError(f"{family_key}: 출시 전 관측에 쓸 원본 선행 릴리즈가 없습니다.")
        index, target = max(available, key=lambda pair: pair[1]["order"])
    return {"family": family_key, "version": target["name"], "release": index,
            "workbook": target.get("workbook", family["workbook"]),
            "sheet": target.get("sheet", family["sheet"]), "cell": target["cell"]}


def _unused_version(family, basis, *, direction=1, start=1, reserved=()):
    """원본과 같은 숫자·구분자·접미사 형식으로 충돌 없는 검증용 원문을 만든다."""
    matches = list(re.finditer(r"\d+", basis))
    if not matches:
        raise ValueError("버전 원문에 숫자 표기 근거가 없습니다.")
    match = matches[-1]
    used = {version["name"].strip().casefold() for version in family["versions"]} | {s.strip().casefold() for s in reserved}
    for step in range(start, start + 1000):
        number = int(match[0]) + direction * step
        if number < 0:
            break
        raw = basis[:match.start()] + str(number) + basis[match.end():]
        if raw.strip().casefold() not in used:
            return raw
    raise ValueError("원본 버전 패턴 안에서 충돌 없는 검증용 버전을 만들 수 없습니다.")


def apply_scenarios(source_catalog, catalog, plan):
    """추정 관측은 별도 workbook에 보존하고 core가 emit할 위험 규칙 사양을 반환한다."""
    workbook = len(catalog["workbooks"])
    sheets = {}
    book = {"name": "SETRON 운영 상태 검증 근거", "synthetic": True, "notice": NOTICE, "sheets": sheets}
    catalog["workbooks"].append(book)
    metadata = {"notice": NOTICE, "workbook": workbook, "mixed_pm": {}, "pm_mismatch": {},
                "unregistered": [], "aging": [], "risk_rules": []}

    def record(sheet, fields):
        cells = sheets.setdefault(sheet, {})
        row = 1 + max((int(re.search(r"\d+$", address)[0]) for address in cells), default=0)
        result = {}
        for column, (name, value) in enumerate(fields.items()):
            letter = chr(ord("A") + column)
            if row == 1:
                cells[f"{letter}1"] = name
            cells[f"{letter}{max(2, row)}"] = value
            result[name] = f"{letter}{max(2, row)}"
        return result

    # ESON81의 PM 이름·모델 배정은 원본 P57 그대로, 출시 전 월의 SW만 선행 릴리즈로 재구성한다.
    mixed = next(entry for entry in plan if entry.get("scenario_key") == "original_mixed_pm")
    original_book, _, o3_raw, o2_raw = _mixed_source(source_catalog)
    mixed_adjustments = []
    for observation in mixed["observations"]:
        software = []
        for key, model, raw, names in (("o3", "M-O3", o3_raw, ("PM1", "PM2")),
                                       ("o2_foundry", "M-O2", o2_raw, ("PM3", "PM4"))):
            sw = _release_observation(catalog, key, model, raw, observation["date"])
            if sw["version"] == raw:
                sw.update(workbook=original_book, sheet="Foundry", cell="G57")
            else:
                mixed_adjustments.append({"date": observation["date"], "family": key, "version": sw["version"],
                                          "reason": "원본 관측 SW의 실제 출시 전 월은 선행 릴리즈로 재구성"})
            sw["pms"] = [{"name": name, "model": model, "model_raw": model.removeprefix("M-"), "workbook": original_book,
                          "sheet": "Foundry", "source_reference": {
                              "pm_name": {"sheet": "Foundry", "cells": ["P57"]},
                              "model_name_raw": {"sheet": "Foundry", "cells": ["C57", "P57"]}}}
                         for name in names]
            software.append(sw)
        observation["software"] = software
    metadata["mixed_pm"] = {"equipment_code": mixed["code"], "source": _location(original_book, "Foundry", "G57", "P57"),
                             "historical_inference": mixed_adjustments, "pm_count_per_snapshot": 4}

    reserved_entries = {mixed["code"]}

    def select(model, family):
        entry = next((entry for entry in plan if entry["code"] not in reserved_entries
                      and (_template(catalog, entry)["model"], _template(catalog, entry)["family"]) == (model, family)), None)
        if entry is None:
            raise ValueError(f"{model}/{family}: 검증 관측을 배치할 설비가 없습니다.")
        reserved_entries.add(entry["code"])
        return entry

    # 동일 계열의 서로 다른 PM 버전도 집계에서 제외하지 않고 표시하는 분기.
    mismatch = select("M-C4", "c4")
    for observation in mismatch["observations"]:
        primary = {key: observation[key] for key in ("version", "release", "workbook", "sheet", "cell")}
        sw = {"family": "c4", **primary, "pms": []}
        for name in ("PM1", "PM2"):
            cells = record("PM 버전 비교", {"설비 호기": mismatch["code"], "기준일": observation["date"],
                            "PM": name, "모델": "M-C4", "SW 버전": observation["version"], "판정 근거": NOTICE})
            sw["pms"].append({"name": name, "model": "M-C4", "model_raw": "M-C4", "workbook": workbook,
                              "sheet": "PM 버전 비교", "source_reference": {
                                  "pm_name": {"sheet": "PM 버전 비교", "cells": [cells["PM"]]},
                                  "model_name_raw": {"sheet": "PM 버전 비교", "cells": [cells["모델"]]}}})
        observation["software"] = [sw]
    latest = mismatch["observations"][-1]
    family = catalog["families"]["c4"]
    choices = [(i, version) for i, version in enumerate(family["versions"])
               if version["date"] <= latest["date"] and not version.get("variant")
               and any("M-C4" in family["items"][n]["models"] for n in version["items"])]
    choices.sort(key=lambda pair: pair[1]["order"])
    if len(choices) < 3:
        raise ValueError("C4 PM 불일치를 보여 줄 원본 릴리즈가 부족합니다.")
    low, high = choices[-3], choices[-2]
    split = []
    for position, (index, version) in enumerate((low, high)):
        pm = deepcopy(latest["software"][0]["pms"][position])
        cells = record("PM 버전 비교", {"설비 호기": mismatch["code"], "기준일": latest["date"],
                        "PM": pm["name"], "모델": "M-C4", "SW 버전": version["name"], "판정 근거": NOTICE})
        split.append({"family": "c4", "version": version["name"], "release": index, "workbook": workbook,
                      "sheet": "PM 버전 비교", "cell": cells["SW 버전"], "pms": [pm]})
    latest["software"] = split
    metadata["pm_mismatch"] = {"equipment_code": mismatch["code"], "family": "c4", "date": latest["date"],
                                "versions": [version["name"] for _, version in (low, high)], "synthetic": True}

    # 미등록 원문은 기존 릴리즈를 만들거나 별칭으로 연결하지 않은 채 마지막 관측에 남긴다.
    for model, key in (("M-C3", "c3"), ("M-C4", "c4"), ("M-O2", "o2")):
        entry = select(model, key)
        observation = entry["observations"][-1]
        previous = observation["version"]
        raw = _unused_version(catalog["families"][key], previous)
        cells = record("미등록 SW 검토", {"설비 호기": entry["code"], "기준일": observation["date"], "모델": model,
                        "SW 버전": raw, "참고 원문": previous, "판정 근거": NOTICE})
        observation.update(version=raw, release=None, workbook=workbook, sheet="미등록 SW 검토", cell=cells["SW 버전"])
        metadata["unregistered"].append({"equipment_code": entry["code"], "family": key, "version": raw,
                                          "date": observation["date"], "source": _location(workbook, "미등록 SW 검토", cells["SW 버전"]),
                                          "basis_version": previous, "synthetic": True})

    # 원본 날짜는 바꾸지 않고 별도 과거 릴리즈 두 개로 1년·2년 노후도 경계를 검증한다.
    key, model = "o2", "M-O2"
    family = catalog["families"][key]
    first = min(source_catalog["families"][key]["versions"], key=lambda version: version["order"])
    original_item = source_catalog["families"][key]["items"][first["items"][0]]
    minimum_order = min(version["order"] for version in family["versions"])
    if minimum_order < 3:
        raise ValueError("기존 릴리즈 순서를 바꾸지 않고 과거 검증 버전을 넣을 수 없습니다.")
    for status, released_at, offset, order in (("DANGER", "2024-04-21", 2, minimum_order - 2),
                                               ("WARNING", "2025-04-21", 1, minimum_order - 1)):
        entry = select(model, key)
        raw = _unused_version(family, first["name"], direction=-1, start=offset)
        item_no = max((item["no"] or 0 for item in family["items"]), default=0) + 1
        content = {"DANGER": "[Engine] Trace Data 저장 실패 시 디스크 상태 재확인 로직 보완",
                   "WARNING": "[Engine] Trace Data 저장 오류 발생 시 복구 및 재시도 조건 개선"}[status]
        if content in {item["content"] for item in family["items"]}:
            raise ValueError("장기 운영 검증 Item 문구가 기존 Item과 중복됩니다.")
        cells = record("장기 운영 릴리즈", {"SW 버전": raw, "출시일": released_at, "비교 순서": order,
                        "Item 번호": item_no, "변경 내용": content, "적용 모델": model,
                        "판정 근거": NOTICE, "참고 원문": first["name"]})
        item_index, release_index = len(family["items"]), len(family["versions"])
        inference = {"kind": "노후도 경계와 직접 버전 위험 규칙의 승인된 검증 자료", "synthetic": True,
                     "not_product_release_or_defect": True, "source_family": key,
                     "source_workbook": source_catalog["families"][key]["workbook"],
                     "source_sheet": source_catalog["families"][key]["sheet"], "source_cell": original_item["cell"]}
        family["items"].append({"no": item_no, "content": content, "models": [model], "marks": [],
                                "workbook": workbook, "sheet": "장기 운영 릴리즈", "cell": cells["변경 내용"],
                                "model_cell": cells["적용 모델"], "synthetic": True, "inference": inference})
        family["versions"].append({"name": raw, "date": released_at, "order": order, "variant": "", "items": [item_index],
                                   "workbook": workbook, "sheet": "장기 운영 릴리즈", "cell": cells["SW 버전"],
                                   "date_cell": cells["출시일"], "order_cell": cells["비교 순서"], "synthetic": True, "inference": inference})
        for observation in entry["observations"]:
            observation.update(version=raw, release=release_index, workbook=workbook, sheet="장기 운영 릴리즈", cell=cells["SW 버전"])
        metadata["aging"].append({"equipment_code": entry["code"], "family": key, "version": raw,
                                   "released_at": released_at, "expected_status": status, "synthetic": True})
        risk_reason = "지정 버전 판정과 우선순위 확인을 위해 등록한 검증 기준"
        metadata["risk_rules"].append({"family": key, "release": release_index, "model": model,
            "rule_type": "DIRECT_VERSION", "risk_reason": risk_reason,
            "target_reference": {"synthetic": True, "not_product_defect": True, "purpose": NOTICE,
                                 "scenario": "DIRECT_VERSION_VALIDATION", "source": _location(workbook, "장기 운영 릴리즈", cells["SW 버전"]),
                                 "inference": inference}})

    book["sha256"] = hashlib.sha256(json.dumps(sheets, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
    return metadata
