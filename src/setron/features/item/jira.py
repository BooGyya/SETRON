"""릴리즈노트 Item의 Jira 코드 (11/11 멘토링: 버전 · Item · Item별 Jira 코드가 함께 다니는 필수 정보).

원본 표기에서 읽는 규칙은 아이템 매니저의 원본 행 표시(:mod:`setron.ui.item_original`)와 같다.
- Jira 열(머리글에 'jira' · issue · ticket · 항목ID): 쉼표 · 줄바꿈 · 세미콜론으로 나눈 원문 조각 그대로.
- 비고 열(REMARK · 비고): 자유 문장이라 그 안에 실제로 적힌 Jira 키 모양(ETOJ-1234)만.
- '-' · 없음 · 미기재 같은 빈 칸 표기는 코드가 아니다. 원본에 없는 코드는 만들지 않는다.

저장할 때는 여러 코드를 ', '로 잇는다(아이템 표 ``jira_code``, 최대 :data:`MAX_LENGTH`자).
"""

from __future__ import annotations

import re

MAX_LENGTH = 200
JIRA_HEADERS = frozenset(("issue", "issueid", "issuekey", "ticket", "항목id"))
REMARK_HEADERS = frozenset(("remark", "remarks", "비고"))
JIRA_KEY = re.compile(r"(?<![A-Za-z0-9_-])[A-Z][A-Z0-9_]+-\d+(?![A-Za-z0-9_-])")
BLANK_VALUES = frozenset(("", "-", "—", "n/a", "na", "없음", "미기재"))


def header_key(text) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def header_kind(header) -> str | None:
    """'jira'(Jira 열) · 'remark'(비고 열) · None."""
    key = header_key(header)
    if "jira" in key or key in JIRA_HEADERS:
        return "jira"
    if key in REMARK_HEADERS:
        return "remark"
    return None


def codes_in(value, header) -> tuple[str, ...]:
    """한 칸에서 읽은 Jira 코드(원문 순서, 중복 제거). 머리글 종류에 따라 읽는 법이 다르다."""
    text = "" if value is None else str(value)
    kind = header_kind(header)
    if kind == "jira":
        parts = [part.strip() for part in re.split(r"[,;\n]", text)]
        found = [part for part in parts if part and part.casefold() not in BLANK_VALUES]
    elif kind == "remark":
        found = JIRA_KEY.findall(text)
    else:
        found = []
    return tuple(dict.fromkeys(found))


def join_codes(codes) -> str | None:
    """저장용 문자열. 코드가 없으면 None."""
    codes = tuple(dict.fromkeys(code.strip() for code in codes if code and code.strip()))
    return ", ".join(codes)[:MAX_LENGTH] if codes else None


def split_codes(stored) -> tuple[str, ...]:
    """저장된 ``jira_code`` → 코드 목록."""
    return tuple(part.strip() for part in re.split(r"[,;\n]", stored or "") if part.strip())
