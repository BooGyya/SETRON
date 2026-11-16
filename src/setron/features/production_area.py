"""생산 분류의 표시 이름과 저장 코드를 분리한다."""

import re

PRODUCTION_AREA_LABELS = {"MEMORY": "Memory", "FOUNDRY": "Foundry"}
_VALUES = {"memory": "MEMORY", "foundry": "FOUNDRY", "메모리": "MEMORY", "파운드리": "FOUNDRY"}
_managed_labels = dict(PRODUCTION_AREA_LABELS)


def use_area_labels(labels):
    """채택한 DB 카탈로그의 표시 이름. 내부 업무 코드는 바꾸지 않는다."""
    global _managed_labels
    _managed_labels = dict(labels)


def area_choices():
    return tuple(_managed_labels.items())


def production_area_value(value):
    """화면에서 선택·입력한 이름을 기존 DB 코드로 변환한다. 모르는 값은 검증에 맡긴다."""
    if not isinstance(value, str):
        return value
    if value in _managed_labels or value in PRODUCTION_AREA_LABELS:
        return value
    text = value.strip().casefold()
    labels = {label.casefold(): code for code, label in _managed_labels.items()}
    return labels.get(text, _VALUES.get(text, value))


def production_area_label(value, *, unknown=""):
    """안정적인 내부 코드에 대응하는 관리 이름을 표시한다."""
    return _managed_labels.get(production_area_value(value), value or unknown)


def production_area_text(text):
    """계열 이름 등 표시용 분류 문구만 통일한다. 식별·저장·원본 비교에는 사용하지 않는다."""
    text = re.sub(r"메모리\s*\(\s*memory\s*\)", "Memory", text, flags=re.IGNORECASE)
    text = re.sub(r"파운드리\s*\(\s*foundry\s*\)", "Foundry", text, flags=re.IGNORECASE)
    text = text.replace("메모리", "Memory").replace("파운드리", "Foundry")
    return re.sub(r"(?<![a-z])(?:memory|foundry)(?![a-z])",
                  lambda match: match.group().capitalize(), text, flags=re.IGNORECASE)
