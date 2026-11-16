"""DB에서 관리하는 분류 이름과 명시적인 Line → 세부 Line 관계."""

from __future__ import annotations

import re

CATEGORIES_KEY = "hierarchy.categories.v1"
AREAS_KEY = "hierarchy.areas.v1"
MODEL_ALIASES_KEY = "hierarchy.model_aliases.v1"
DEFAULT_CATEGORIES = {"POLY": "POLY", "OXIDE": "OXIDE", "KCTC": "KCTC", "HPA": "HPA"}
DEFAULT_AREAS = {"MEMORY": "Memory", "FOUNDRY": "Foundry"}


def _labels(tx, key, defaults):
    try:
        value = tx.get("app_setting", {"setting_key": key})["setting_value"]
    except LookupError:
        return dict(defaults)
    if not isinstance(value, dict):
        return dict(defaults)
    removed = value.get("deleted", ())
    removed = removed if isinstance(removed, (list, tuple)) else ()
    labels = {code: label for code, label in defaults.items() if code not in removed}
    saved = value.get("labels", {})
    saved = saved if isinstance(saved, dict) else {}
    labels.update({code: label for code, label in saved.items()
                   if isinstance(code, str) and isinstance(label, str) and label.strip()})
    return labels


def category_labels(tx) -> dict[str, str]:
    """표시 이름이 바뀌어도 DB의 분류 코드는 유지한다."""
    return _labels(tx, CATEGORIES_KEY, DEFAULT_CATEGORIES)


def area_labels(tx) -> dict[str, str]:
    return _labels(tx, AREAS_KEY, DEFAULT_AREAS)


def model_aliases(tx) -> dict[str, int]:
    """이름을 바꾸기 전 모델 표기도 같은 DB 모델 ID로 연결한다."""
    try:
        value = tx.get("app_setting", {"setting_key": MODEL_ALIASES_KEY})["setting_value"]
    except LookupError:
        return {}
    aliases = value.get("aliases", {}) if isinstance(value, dict) else {}
    aliases = aliases if isinstance(aliases, dict) else {}
    return {name: group_id for name, group_id in aliases.items()
            if isinstance(name, str) and type(group_id) is int and group_id > 0}


def _clean_line(value):
    return " ".join(re.sub(r"\(\s*\d+\s*[Ff]?\s*\)", "", value or "").split())


def legacy_line_parts(value) -> tuple[str | None, str | None]:
    """관계를 아직 설정하지 않은 기존 DB의 표시 결과만 호환한다."""
    cleaned = _clean_line(value)
    if not cleaned:
        return None, None
    match = re.fullmatch(r"(P\d+)\s*([FD])", cleaned, flags=re.IGNORECASE)
    if match:
        parent, suffix = (part.upper() for part in match.groups())
        return parent, parent + suffix
    if re.fullmatch(r"P4\s*H", cleaned, flags=re.IGNORECASE):
        return "P4", "P4H"
    return cleaned, None


def line_parts(site, value, entries) -> tuple[str | None, str | None]:
    """명시한 관계를 우선한다. parent_line=None은 이름과 관계없이 일반 Line이다."""
    cleaned = _clean_line(value)
    site_key = (site or "").strip().casefold()
    for entry in entries:
        if ((entry.get("site") or "").strip().casefold() == site_key
                and _clean_line(entry.get("line")).casefold() == cleaned.casefold()
                and "parent_line" in entry):
            parent = entry["parent_line"]
            return (parent, entry["line"]) if parent else (entry["line"], None)
    return legacy_line_parts(value)
