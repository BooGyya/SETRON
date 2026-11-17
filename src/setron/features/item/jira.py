"""Item Jira 코드와 원본 근거. 단일 추출 API와 복수 코드 저장 API를 함께 제공한다."""

from collections import defaultdict
from dataclasses import dataclass
import re

_ISSUE = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*-[0-9]+)(?![A-Za-z0-9_])")
_ADDRESS = re.compile(r"([A-Z]+)([1-9][0-9]*)")
_HEADERS = {"jira", "jirakey", "jiraissue", "jiraissuekey", "jirano", "jira번호", "jira코드", "jira이슈코드", "이슈코드", "remark"}


def extract_jira_key(text):
    """코드가 없으면 None, 같은 코드만 반복되면 한 개, 다른 코드가 여러 개면 오류다."""
    codes = tuple(dict.fromkeys(_ISSUE.findall(text if isinstance(text, str) else "")))
    if len(codes) > 1:
        raise ValueError("Item 하나에 서로 다른 JIRA 이슈 코드가 여러 개 있습니다. 원본을 확인해 주세요.")
    return codes[0] if codes else None


@dataclass(frozen=True)
class JiraEvidence:
    jira_key: str | None
    source_reference: dict | None = None

    @property
    def jira_code(self):
        """새 canonical 이름. jira_key 속성은 기존 단일 추출 호출자를 위해 보존한다."""
        return self.jira_key


def extract_jira_from_source(raw_data, source_reference, *, content=None, strict=True):
    """평면/시드 중첩 원본의 JIRA·REMARK 열과 내용 셀을 함께 검사한다.

    반환 출처는 jira_key 필드만 포함한다. 기존 출처에 합쳐 저장하며 원문을 지우거나
    없는 코드를 생성하지 않는다. 원본 빈칸은 출처를 꾸며 만들지 않고 None으로 둔다.
    """
    raw = raw_data if isinstance(raw_data, dict) else {}
    nested = raw.get("sheets")
    sheets = nested if isinstance(nested, dict) and any(isinstance(value, dict) for value in nested.values()) else raw
    if isinstance(raw.get("sheet"), str) and isinstance(raw.get("cells"), dict):
        sheets = {raw["sheet"]: raw["cells"]}
    found, locations = [], defaultdict(list)

    def take(sheet, address, value, header=None):
        codes = ((extract_jira_key(value),) if strict else codes_in(value, header) if header else
                 tuple(_ISSUE.findall(value if isinstance(value, str) else "")))
        codes = tuple(code for code in codes if code)
        if codes:
            found.extend(codes)
            if address not in locations[sheet]:
                locations[sheet].append(address)

    reference = source_reference or {}
    for field in ("jira_key", "jira_code", "content"):
        values = reference.get(field)
        for location in values if isinstance(values, list) else [values] if isinstance(values, dict) else []:
            sheet_name = location.get("sheet")
            cells = sheets.get(sheet_name, {})
            if not isinstance(cells, dict):
                continue
            for address in location.get("cells", []):
                take(sheet_name, address, cells.get(address), "JIRA" if field != "content" else None)
                match = _ADDRESS.fullmatch(address)
                if field != "content" or match is None:
                    continue
                row = int(match[2])
                columns = {}
                for header_address, value in cells.items():
                    if not isinstance(value, str) or len(value) > 32:
                        continue
                    header = _ADDRESS.fullmatch(header_address)
                    if header and int(header[2]) < row and header_kind(value):
                        if int(header[2]) > columns.get(header[1], (0, ""))[0]:
                            columns[header[1]] = (int(header[2]), value)
                for column, (_, label) in columns.items():
                    jira_address = column + str(row)
                    take(sheet_name, jira_address, cells.get(jira_address), label)
    code = (extract_jira_key(" ".join([*found, content if isinstance(content, str) else ""])) if strict else
            normalize_jira(join_codes([*found, *_ISSUE.findall(content if isinstance(content, str) else "")])))
    references = [{"sheet": sheet, "cells": cells} for sheet, cells in locations.items()]
    field = "jira_key" if strict else "jira_code"
    return JiraEvidence(code, {field: references[0] if len(references) == 1 else references} if references else None)


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
    if "jira" in key or "지라" in key or key in JIRA_HEADERS:          # JIRA · JIRA ID · 지라 · Issue(O2)
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
    value = ", ".join(codes) if codes else None
    if value is not None and len(value) > MAX_LENGTH:
        raise ValueError("Jira 코드는 200자 이내여야 합니다. 원문을 자르지 않고 저장을 중단합니다.")
    return value


def split_codes(stored) -> tuple[str, ...]:
    """저장된 ``jira_code`` → 코드 목록."""
    return tuple(part.strip() for part in re.split(r"[,;\n]", stored or "") if part.strip())


def normalize_jira(value) -> str | None:
    """정식 저장값을 무손실 정규화한다. 원문 순서·대소문자는 유지하며 초과값은 거부한다."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Jira 코드는 문자열 또는 None이어야 합니다.")
    codes = tuple(code for code in split_codes(value) if code.casefold() not in BLANK_VALUES)
    return join_codes(codes)


def matches_source(value, original) -> bool:
    """정규화한 각 조각이 같은 원본 셀에 실제로 있는지 확인한다. 키의 일부 일치는 거부한다."""
    if not isinstance(value, str):
        return False
    text = "" if original is None else str(original)
    wanted = split_codes(value)
    available = {*split_codes(text), *_ISSUE.findall(text)}
    return bool(wanted) and all(code in available for code in wanted)
