"""정해진 양식(설비 현황표·세로형 릴리즈노트)을 AI 없이 규칙으로 읽는다. 처음 보는 양식은 AI 추출로 넘긴다."""

from .service import RULE_MESSAGE, rule_extract

__all__ = ["RULE_MESSAGE", "rule_extract"]
