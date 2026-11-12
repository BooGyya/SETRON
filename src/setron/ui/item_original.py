"""릴리즈 노트의 보관 원본 행. O5/C4 열을 같은 모양으로 강제 변환하지 않는다."""

from dataclasses import dataclass
from datetime import date, datetime
import re


@dataclass(frozen=True)
class OriginalItemRow:
    item_id: int
    batch_id: int
    sheet: str
    row: int
    headers: tuple[str, ...]
    values: tuple[str, ...]

    @property
    def document(self):
        return f"입력 작업 {self.batch_id} · {self.sheet}"


JIRA_FOUND, JIRA_EMPTY, JIRA_NO_COLUMN, JIRA_NO_ORIGINAL = "found", "empty", "no_column", "no_original"
# Jira 열로 보는 머리글(공백 제거·소문자). 릴리즈노트 규칙 추출의 jira 별칭에서 비고(REMARK)는 따로 다룬다.
_JIRA_HEADERS = frozenset(("issue", "issueid", "issuekey", "ticket", "항목id"))
# 비고(REMARK) 열은 자유 문장이라 그 안에 실제로 적힌 Jira 키 모양의 부분 문자열만 쓴다.
_REMARK_HEADERS = frozenset(("remark", "remarks", "비고"))
_JIRA_KEY = re.compile(r"(?<![A-Za-z0-9_-])[A-Z][A-Z0-9_]+-\d+(?![A-Za-z0-9_-])")
_BLANK_VALUES = frozenset(("", "-", "—", "n/a", "na", "없음", "미기재"))


@dataclass(frozen=True)
class ItemJira:
    """원본 행에서 읽은 Item의 Jira 코드. 값은 원본 셀 문자열 그대로이며 만들거나 고치지 않는다.

    ivar
    - codes: 원본에 적힌 Jira 코드(중복 제거, 원본 순서).
    - status: found(코드 있음)·empty(Jira 열은 있으나 빈 칸)·no_column(원본 행에 Jira 열 없음)·no_original(보관 원본 행 없음).
    - sources: 코드가 나온 '입력 작업 N · 시트 · 행' 위치.
    """

    codes: tuple[str, ...]
    status: str
    sources: tuple[str, ...] = ()
    stored: bool = False

    @property
    def label(self):
        """목록·상세에 쓰는 짧은 표시. 코드가 없으면 없음/미확인을 구분한다."""
        if self.codes:
            return ", ".join(self.codes)
        return "Jira 없음" if self.status == JIRA_EMPTY else "Jira 미확인"

    @property
    def row_text(self):
        """목록 행의 Jira 칸 문구."""
        return f"JIRA {self.label}" if self.codes else self.label

    @property
    def tooltip(self):
        if self.stored:
            return ("JIRA 이슈코드: " + ", ".join(self.codes) + ("\n" + "\n".join(self.sources) if self.sources else "")) if self.codes else "저장된 JIRA 이슈코드가 없습니다."
        if self.codes:
            return "릴리즈노트 원본 Jira: " + ", ".join(self.codes) + ("\n" + "\n".join(self.sources) if self.sources else "")
        return {JIRA_EMPTY: "릴리즈노트 원본의 Jira 칸이 비어 있습니다.",
                JIRA_NO_COLUMN: "릴리즈노트 원본 행에 Jira 열이 없어 확인할 수 없습니다.",
                JIRA_NO_ORIGINAL: "보관된 릴리즈노트 원본 행이 없어 Jira를 확인할 수 없습니다."}[self.status]


def _header_key(text):
    return re.sub(r"\s+", "", text).casefold()


def _jira_column(header):
    key = _header_key(header)
    return "jira" in key or key in _JIRA_HEADERS


def _jira_cell_codes(value):
    """Jira 열 값: 쉼표·줄바꿈·세미콜론으로 나뉜 원문 조각. 빈 칸 표기(-, 없음 등)는 코드가 아니다."""
    return [part.strip() for part in re.split(r"[,;\n]", value)
            if part.strip() and part.strip().casefold() not in _BLANK_VALUES]


def item_jira(rows, item_id):
    """보관된 원본 행에서 한 Item의 Jira 코드를 읽는다. 원본에 없는 값은 만들지 않는다."""
    mine = [r for r in rows if r.item_id == item_id]
    if not mine:
        return ItemJira((), JIRA_NO_ORIGINAL)
    codes, sources, has_column = [], [], False
    for row in mine:
        found = []
        for header, value in zip(row.headers, row.values):
            if _jira_column(header):
                has_column = True
                found.extend(_jira_cell_codes(value))
            elif _header_key(header) in _REMARK_HEADERS:
                found.extend(_JIRA_KEY.findall(value))
        if found:
            sources.append(f"{row.document} · {row.row}행")
        codes.extend(found)
    codes = tuple(dict.fromkeys(codes))
    if codes:
        return ItemJira(codes, JIRA_FOUND, tuple(dict.fromkeys(sources)))
    return ItemJira((), JIRA_EMPTY if has_column else JIRA_NO_COLUMN)


def jira_by_item(rows, item_ids):
    """여러 Item의 Jira를 한 번에 읽는다. 원본 행을 Item별로 한 번만 나눈다."""
    grouped = {}
    for row in rows:
        grouped.setdefault(row.item_id, []).append(row)
    return {item_id: item_jira(grouped.get(item_id, ()), item_id) for item_id in item_ids}


def _text(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else "" if value is None else str(value)


def _column_number(value):
    number = 0
    for char in value:
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def original_item_rows(tx, items):
    """출처가 가리키는 실제 원본 행의 모든 열을 읽는다. 없는 문서명/셀/열은 만들지 않는다."""
    batches, sheets, result, seen = {}, {}, [], set()
    for item in items:
        for field, references in (item.get("source_reference") or {}).items():
            if field not in {"content", "내용", "Update내용", "update_content"}:
                continue
            for ref in references if isinstance(references, list) else (references,):
                bid = ref.get("batch_id", item["batch_id"])
                if bid not in batches:
                    batches[bid] = tx.get("import_batch", {"batch_id": bid})["raw_data"]
                sheet_key = bid, ref["sheet"]
                if sheet_key not in sheets:
                    sheet = (batches[bid] or {}).get(ref["sheet"], {})
                    cells = {}
                    for address, value in sheet.items():
                        match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", address)
                        if match:
                            cells.setdefault(int(match[2]), {})[match[1]] = value
                    headers = [r for r, values in cells.items() if any(
                        re.sub(r"\s+", "", _text(v)).casefold() in {"내용", "update내용", "update", "변경내용"}
                        for v in values.values())]
                    sheets[sheet_key] = cells, headers
                cells, header_rows = sheets[sheet_key]
                rows = {int(match[1]) for address in ref["cells"]
                        if (match := re.fullmatch(r"[A-Z]+([1-9][0-9]*)", address))}
                for row in sorted(rows):
                    key = item["item_id"], bid, ref["sheet"], row
                    if key in seen or row not in cells:
                        continue
                    seen.add(key)
                    # 실제 내용/Update 헤더가 있는 가장 가까운 이전 행만 헤더로 쓴다.
                    candidates = [r for r in header_rows if r < row]
                    header = cells[max(candidates)] if candidates else {}
                    columns = sorted(set(cells[row]) | set(header), key=_column_number)
                    result.append(OriginalItemRow(item["item_id"], bid, ref["sheet"], row,
                        tuple(_text(header.get(c)) or c for c in columns),
                        tuple(_text(cells[row].get(c)) for c in columns)))
    return tuple(result)
