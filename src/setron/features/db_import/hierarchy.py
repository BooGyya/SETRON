"""SQL 병합에서 계층 설정을 함께 보존하고 코드의 의미 충돌을 차단한다."""

from __future__ import annotations

import json
import re

from setron.features.hierarchy import DEFAULT_AREAS, DEFAULT_CATEGORIES, _clean_line, line_parts
from setron.features.storage.inputs import require


CATEGORIES_KEY = b"hierarchy.categories.v1"
AREAS_KEY = b"hierarchy.areas.v1"
ALIASES_KEY = b"hierarchy.model_aliases.v1"
LINES_KEY = b"registered_lines.v1"
SITES_KEY = b"registered_sites.v1"
HIERARCHY_KEYS = frozenset((CATEGORIES_KEY, AREAS_KEY, ALIASES_KEY, LINES_KEY, SITES_KEY))


def _key(value):
    return value.encode("utf-8") if isinstance(value, str) else value


def _value(row):
    value = row["setting_value"]
    try:
        value = json.loads(value) if isinstance(value, (str, bytes)) else value
    except (ValueError, UnicodeError):
        require(False, "계층 설정의 JSON 형식이 올바르지 않습니다.")
    require(isinstance(value, dict), "계층 설정은 객체 형식이어야 합니다.")
    return value


def _text(value, title):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= 50,
            f"{title}을 50자 이내로 입력해야 합니다.")
    return value.strip()


def _labels(value, defaults, prefix):
    labels, removed = value.get("labels", {}), value.get("deleted", [])
    require(isinstance(labels, dict) and isinstance(removed, list)
            and all(isinstance(code, str) and code in defaults for code in removed)
            and not set(labels).intersection(removed), "계층 분류 설정 형식이 올바르지 않습니다.")
    result = {code: label for code, label in defaults.items() if code not in removed}
    for code, label in labels.items():
        require(isinstance(code, str) and (code in defaults or re.fullmatch(prefix + r"[0-9]{9}", code)),
                "계층 분류 코드가 올바르지 않습니다.")
        result[code] = _text(label, "분류 이름")
    names = [label.casefold() for label in result.values()]
    require(len(names) == len(set(names)), "서로 다른 분류 코드에 같은 이름이 있어 병합할 수 없습니다.")
    reserved = {code.casefold(): code for code in defaults.keys() | result.keys()}
    if prefix == "A":
        reserved.update({"메모리": "MEMORY", "파운드리": "FOUNDRY"})
    for code, label in result.items():
        reserved_code = reserved.get(label.casefold())
        require((reserved_code is None or reserved_code == code) and
                (not re.fullmatch(r"[CA][0-9]{9}", label, flags=re.IGNORECASE) or label.casefold() == code.casefold()),
                "다른 분류의 내부 코드나 예약 이름은 표시 이름으로 사용할 수 없습니다.")
    return result


def _sites(value):
    values = value.get("sites", [])
    require(isinstance(values, list), "등록 사업장 설정 형식이 올바르지 않습니다.")
    result = {}
    for name in values:
        name = _text(name, "사업장 이름")
        require(name.casefold() not in result, "등록 사업장 이름이 중복되었습니다.")
        result[name.casefold()] = name
    return result


def _lines(value):
    values = value.get("lines", [])
    require(isinstance(values, list), "등록 라인 설정 형식이 올바르지 않습니다.")
    result = {}
    for entry in values:
        require(isinstance(entry, dict), "등록 라인 설정 형식이 올바르지 않습니다.")
        site, line = _text(entry.get("site"), "사업장 이름"), _text(entry.get("line"), "라인 이름")
        require(entry.get("fab") is None or isinstance(entry["fab"], str), "라인의 생산 분류 코드가 올바르지 않습니다.")
        normalized = {**entry, "site": site, "line": line, "fab": entry.get("fab")}
        if entry.get("parent_line") is not None:
            normalized["parent_line"] = _text(entry["parent_line"], "상위 라인 이름")
        key = site.casefold(), _clean_line(line).casefold()
        require(key not in result, "같은 사업장의 등록 라인 이름이 중복되었습니다.")
        result[key] = normalized
    return result


def _aliases(value):
    values = value.get("aliases", {})
    require(isinstance(values, dict), "모델 이전 이름 설정 형식이 올바르지 않습니다.")
    result = {}
    for alias, group_id in values.items():
        alias = _text(alias, "모델 이전 이름")
        require(type(group_id) is int and group_id > 0, "모델 이전 이름의 연결 ID가 올바르지 않습니다.")
        require(alias.casefold() not in result, "모델 이전 이름이 중복되었습니다.")
        result[alias.casefold()] = alias, group_id
    return result


def merge_hierarchy_setting(row, existing, mode):
    """추가 모드도 서로 다른 등록 항목은 합친다. 사용자 정의 코드 충돌은 거부한다."""
    key = _key(row["setting_key"])
    if key not in HIERARCHY_KEYS:
        return row
    source = _value(row)
    target = source if existing is None else _value(existing)
    merged = {**source, **target} if mode == "add" else {**target, **source}
    if key in (CATEGORIES_KEY, AREAS_KEY):
        defaults, prefix = (DEFAULT_CATEGORIES, "C") if key == CATEGORIES_KEY else (DEFAULT_AREAS, "A")
        incoming, current = _labels(source, defaults, prefix), _labels(target, defaults, prefix)
        for code in (incoming.keys() & current.keys()) - defaults.keys():
            require(incoming[code] == current[code],
                    f"사용자 정의 분류 코드 {code}의 이름이 서로 달라 SQL을 병합할 수 없습니다.")
        labels = {**incoming, **current} if mode == "add" else {**current, **incoming}
        removed = (target.get("deleted", []) if mode == "add" else
                   [*source.get("deleted", []), *(code for code in target.get("deleted", [])
                                                 if code not in source.get("labels", {}))])
        for code in removed:
            labels.pop(code, None)
        _labels({"labels": labels, "deleted": [code for code in defaults if code not in labels]}, defaults, prefix)
        merged.update(labels=labels, deleted=[code for code in defaults if code not in labels])
        sequences = [value.get("sequence", 0) for value in (source, target)]
        require(all(type(number) is int and number >= 0 for number in sequences), "분류 설정 번호가 올바르지 않습니다.")
        merged["sequence"] = max(sequences)
    else:
        reader, field = {SITES_KEY: (_sites, "sites"), LINES_KEY: (_lines, "lines"),
                         ALIASES_KEY: (_aliases, "aliases")}[key]
        incoming, current = reader(source), reader(target)
        entries = {**incoming, **current} if mode == "add" else {**current, **incoming}
        merged[field] = ({alias: group_id for alias, group_id in entries.values()} if key == ALIASES_KEY
                         else list(entries.values()))
    return {**row, "setting_value": json.dumps(merged, ensure_ascii=False, separators=(",", ":"))}


def validate_hierarchy(rows):
    """최종 데이터에서 분류 정의·라인 부모·모델 이전 이름의 연결을 검사한다."""
    settings = {_key(row["setting_key"]): row for row in rows.get("app_setting", ())
                if _key(row["setting_key"]) in HIERARCHY_KEYS}
    values = {key: _value(row) for key, row in settings.items()}
    categories = _labels(values.get(CATEGORIES_KEY, {}), DEFAULT_CATEGORIES, "C")
    areas = _labels(values.get(AREAS_KEY, {}), DEFAULT_AREAS, "A")
    groups = {row["group_id"]: row for row in rows.get("equipment_group", ())}
    for group in groups.values():
        require(group["category"] in categories, "모델이 참조하는 분류 코드의 정의가 SQL 자료에 없습니다.")
        parent_id = group.get("parent_group_id")
        if parent_id is not None:
            require(parent_id in groups and groups[parent_id]["category"] == group["category"],
                    "상위 설비군과 하위 모델의 분류가 다릅니다.")
    snapshots = rows.get("equipment_snapshot", ())
    _sites(values.get(SITES_KEY, {}))
    for row in snapshots:
        require(row.get("production_area") is None or row["production_area"] in areas,
                "설비 현황이 참조하는 생산 분류 코드의 정의가 SQL 자료에 없습니다.")
    lines = _lines(values.get(LINES_KEY, {}))
    entries = tuple(lines.values())
    for (site, name), entry in lines.items():
        # 별도 Site 테이블이 없으므로 등록 라인 자체도 유효한 사업장 등록이다.
        require(entry.get("fab") is None or entry["fab"] in areas, "등록 라인의 생산 분류 코드 정의가 SQL 자료에 없습니다.")
        parent_name = entry.get("parent_line")
        if parent_name is None:
            continue
        parent = lines.get((site, _clean_line(parent_name).casefold()))
        require(parent is not None, "세부 라인의 상위 Line이 SQL 자료에 없습니다.")
        require(parent_name == parent["line"], "세부 라인의 상위 Line 참조 이름이 등록된 이름과 다릅니다.")
        require(parent is not entry and parent.get("parent_line") is None
                and line_parts(parent["site"], parent["line"], entries)[1] is None,
                "라인 부모 관계가 순환하거나 두 단계보다 깊습니다.")
        require(parent["fab"] == entry["fab"], "세부 라인과 상위 Line의 생산 분류가 다릅니다.")
    for row in snapshots:
        entry = lines.get((row["site"].strip().casefold(), _clean_line(row.get("line")).casefold()))
        if entry is not None and "parent_line" in entry and row.get("production_area") is not None:
            require(row["production_area"] == entry["fab"], "설비 현황의 생산 분류가 등록 라인과 다릅니다.")
    aliases = _aliases(values.get(ALIASES_KEY, {}))
    names = {row["group_name"].strip().casefold(): group_id for group_id, row in groups.items()}
    for folded, (_, group_id) in aliases.items():
        require(group_id in groups, "모델 이전 이름이 참조하는 모델이 SQL 자료에 없습니다.")
        require(folded not in names or names[folded] == group_id,
                "모델 이전 이름이 다른 현재 모델 이름과 충돌합니다.")
