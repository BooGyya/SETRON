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
