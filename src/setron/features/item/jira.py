"""Item 원문의 단일 JIRA 이슈 코드와 원본 셀 근거를 추출한다."""

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


def extract_jira_from_source(raw_data, source_reference, *, content=None):
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

    def take(sheet, address, value):
        code = extract_jira_key(value)
        if code:
            found.append(code)
            if address not in locations[sheet]:
                locations[sheet].append(address)

    reference = source_reference or {}
    for field in ("jira_key", "content"):
        values = reference.get(field)
        for location in values if isinstance(values, list) else [values] if isinstance(values, dict) else []:
            sheet_name = location.get("sheet")
            cells = sheets.get(sheet_name, {})
            if not isinstance(cells, dict):
                continue
            for address in location.get("cells", []):
                take(sheet_name, address, cells.get(address))
                match = _ADDRESS.fullmatch(address)
                if field != "content" or match is None:
                    continue
                row = int(match[2])
                columns = {}
                for header_address, value in cells.items():
                    if not isinstance(value, str) or len(value) > 32:
                        continue
                    header = _ADDRESS.fullmatch(header_address)
                    label = re.sub(r"[\s._-]+", "", value).casefold()
                    if header and int(header[2]) < row and label in _HEADERS:
                        columns[header[1]] = max(columns.get(header[1], 0), int(header[2]))
                for column in columns:
                    jira_address = column + str(row)
                    take(sheet_name, jira_address, cells.get(jira_address))
    code = extract_jira_key(" ".join([*found, content if isinstance(content, str) else ""]))
    references = [{"sheet": sheet, "cells": cells} for sheet, cells in locations.items()]
    return JiraEvidence(code, {"jira_key": references[0] if len(references) == 1 else references} if references else None)
