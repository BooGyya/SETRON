"""공유 노트의 모델별 Item 적용 표식. 버전 전체의 N 후보와 별도로 보관한다.

사용자 확인: NormalC4=M-C4, DFB=M-C4 DFB, DEX=M-C4+.
각 Item 행의 O는 적용, X·공란은 미적용이다. 표식 열 자체가 없는 모델은
미적용으로 만들지 않으며, HW연관·고객사연관 등의 O/X를 모델 표식으로 읽지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
from typing import TYPE_CHECKING

from setron.features.equipment.repository import EquipmentRepository
from setron.features.item.schemas import ItemState
from setron.features.storage.inputs import identifier, require, required_text
if TYPE_CHECKING:
    from setron.infrastructure.excel_reader import SheetData


def _key(value):
    return "".join(value.split()).casefold() if isinstance(value, str) else ""


_HEADERS = {
    "M-C4": {
        **{_key(name): "M-C4" for name in ("NormalC4", "Normal C4", "노말C4", "M-C4", "C4")},
        **{_key(name): "M-C4 DFB" for name in ("DFB", "M-C4 DFB", "C4 DFB")},
        **{_key(name): "M-C4+" for name in ("DEX", "M-C4+", "C4+", "M-C4 Plus", "C4 Plus")},
    },
    "M-O5": {
        **{_key(name): "M-O5" for name in ("M-O5", "O5")},
        **{_key(name): "M-O5 Prime" for name in ("Prime", "O5 Prime", "M-O5 Prime")},
    },
}


def canonical_note_model(value):
    root = {"m-c4": "M-C4", "c4": "M-C4", "m-o5": "M-O5", "o5": "M-O5"}.get(_key(value))
    require(root is not None, "공유 노트 관리 모델은 C4 또는 O5로 확인해 주세요.")
    return root


def canonical_column_model(note_model, header):
    """확정된 구분란만 연결한다. 알 수 없는 열은 None으로 반환한다."""
    return _HEADERS[canonical_note_model(note_model)].get(_key(header))


def _state(value):
    require(value is None or isinstance(value, str), "모델 적용 표식은 O, X 또는 공란이어야 합니다.")
    token = "" if value is None else value.strip()
    require(token in {"O", "X", ""}, "모델 적용 표식은 O, X 또는 공란이어야 합니다.")
    return ItemState.APPLIED if token == "O" else ItemState.NOT_APPLIED


@dataclass(frozen=True)
class ModelItemMark:
    note_model: str
    model_name: str
    state: ItemState
    header_raw: str
    mark_raw: str | None
    sheet_name: str
    header_address: str
    mark_address: str


def read_item_model_marks(sheet: SheetData, *, note_model: str, header_row: int,
                          item_row: int, first_column: int | None = None,
                          last_column: int | None = None) -> tuple[ModelItemMark, ...]:
    """明示したヘッダとItem行のセルだけを読む。原文・空欄・絶対セル位置を保持する。"""
    from setron.infrastructure.excel_reader import SheetData

    require(isinstance(sheet, SheetData), "読み取ったSheetDataが必要です。")
    root = canonical_note_model(note_model)
    require(type(header_row) is int and type(item_row) is int and header_row < item_row,
            "구분란 머리글과 Item 행 번호를 순서대로 지정해 주세요.")
    hi, ri = header_row - sheet.first_row, item_row - sheet.first_row
    require(0 <= hi < sheet.row_count and 0 <= ri < sheet.row_count, "행이 읽은 시트 범위를 벗어났습니다.")
    first = sheet.first_col if first_column is None else first_column
    last = sheet.first_col + len(sheet.values[hi]) - 1 if last_column is None else last_column
    require(type(first) is int and type(last) is int and sheet.first_col <= first <= last,
            "읽을 모델 구분란의 열 범위를 확인해 주세요.")
    require(last < sheet.first_col + len(sheet.values[hi]), "구분란 열이 읽은 범위를 벗어났습니다.")
    result, seen = [], set()
    for column in range(first, last + 1):
        ci = column - sheet.first_col
        header = sheet.values[hi][ci]
        model = canonical_column_model(root, header)
        if model is None:
            continue
        require(model not in seen, "같은 모델의 구분란이 중복됐습니다. 표 범위를 다시 지정해 주세요.")
        require(ci < len(sheet.values[ri]), "모델 적용 셀을 읽지 못했습니다. 공란으로 대신 처리하지 않습니다.")
        seen.add(model)
        raw = sheet.values[ri][ci]
        result.append(ModelItemMark(root, model, _state(raw), header, raw, sheet.sheet_name,
                                    sheet.address_of(hi, ci), sheet.address_of(ri, ci)))
    require(bool(result), "확인된 모델 구분란이 없습니다. 머리글 행과 표 범위를 확인해 주세요.")
    return tuple(result)


@dataclass(frozen=True)
class StoredModelItem:
    model_id: int
    model_name: str
    state: ItemState
    mark: ModelItemMark
    reason: str | None = None


def _fingerprint(value):
    def scalar(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(type(item).__name__)
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, default=scalar)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _matches_item_fingerprint(expected, evidence):
    """컬럼 이름만 이관한 과거 표식 지문도 인정한다. 값 변경은 계속 무효화한다."""
    if expected == _fingerprint(evidence):
        return True
    item = evidence["item"]
    if "jira_code" not in item or "jira_key" in item:
        return False
    previous = {key: value for key, value in item.items() if key != "jira_code"}
    # Jira 열 자체가 없던 표식은 당시 캡처한 필드로 검증한다. 최초 원본 보강도 보존한다.
    candidates = [{**previous, "jira_key": item["jira_code"]}, previous]
    return any(expected == _fingerprint({**evidence, "item": candidate}) for candidate in candidates)


def _marks(values):
    marks = tuple(values)
    require(bool(marks) and all(isinstance(mark, ModelItemMark) for mark in marks), "모델별 Item 적용 정보가 필요합니다.")
    require(len({mark.model_name for mark in marks}) == len(marks), "모델별 Item 적용 정보가 중복됐습니다.")
    roots = {canonical_note_model(mark.note_model) for mark in marks}
    require(len(roots) == 1, "다른 공유 노트의 모델 정보를 한 Item에 합칠 수 없습니다.")
    for mark in marks:
        require(canonical_column_model(mark.note_model, mark.header_raw) == mark.model_name,
                "구분란과 대상 모델이 다릅니다.")
        require(type(mark.state) is ItemState and mark.state is _state(mark.mark_raw), "표식과 적용 상태가 다릅니다.")
        for field in ("sheet_name", "header_address", "mark_address"):
            required_text(getattr(mark, field), field, 255)
    return marks


class SharedNoteItemService:
    """모델별 O/X를 app_setting에 원문·위치와 함께 저장한다. 기존 Item·원본은 바꾸지 않는다.

    X·공란은 NOT_APPLIED이며 NOT_TARGET으로 바꾸지 않는다. 정보가 없거나 저장된
    릴리즈·Item·모델 정의가 바뀐 경우는 미확인이다. N 후보는 이 서비스에서 만들지 않는다.
    """

    def __init__(self, storage):
        self.storage = storage
        self.repository = EquipmentRepository()

    def save(self, release_id, item_id, marks):
        with self.storage.transaction() as tx:
            return self.save_in(tx, release_id, item_id, marks)

    def save_in(self, tx, release_id, item_id, marks):
        identifier(release_id, "release_version_id")
        identifier(item_id, "item_id")
        marks = _marks(marks)
        root = marks[0].note_model
        before = tx.get("release_version", {"release_version_id": release_id})
        family = self.repository.rows(tx, "version_family", lock=True, family_id=before["family_id"])[0]
        release = self.repository.rows(tx, "release_version", lock=True, release_version_id=release_id)[0]
        require(release["family_id"] == family["family_id"], "릴리즈 계열이 변경됐습니다. 다시 검토해 주세요.")
        item = self.repository.rows(tx, "item", lock=True, item_id=item_id)
        require(bool(item), "적용 정보를 연결할 Item이 없습니다.")
        link = tx.get("release_version_item", {"release_version_id": release_id, "item_id": item_id})
        groups = self.repository.rows(tx, "equipment_group", lock=True)
        owner = next(group for group in groups if group["group_id"] == family["group_id"])
        require(canonical_column_model(root, owner["group_name"]) is not None, "공유 노트와 계열 관리 모델이 다릅니다.")
        entries, models = [], []
        for mark in marks:
            found = [group for group in groups if canonical_column_model(root, group["group_name"]) == mark.model_name]
            require(len(found) == 1 and found[0]["category"] == owner["category"] and found[0]["analysis_enabled"],
                    f"{mark.model_name}: 등록 모델 대응이 없거나 중복·분류 오류가 있습니다.")
            models.append(found[0])
            entries.append({"model_id": found[0]["group_id"], "mark": {**asdict(mark), "state": mark.state.value}})
        evidence = {"release": release, "item": item[0], "models": sorted(models, key=lambda value: value["group_id"])}
        key = {"setting_key": f"shared_note_item_models.v1:{release_id}:{item_id}"}
        payload = {"schema": 1, "release_id": release_id, "item_id": item_id,
                   "fingerprint": _fingerprint(evidence), "link_fingerprint": _fingerprint(link), "entries": entries}
        try:
            tx.repository.get(tx.session, "app_setting", key, for_update=True)
        except LookupError:
            tx.insert("app_setting", {**key, "setting_value": payload})
        else:
            tx.update("app_setting", key, {"setting_value": payload})
        return self.load_in(tx, release_id, item_id)

    def load(self, release_id, item_id):
        with self.storage.transaction() as tx:
            return self.load_in(tx, release_id, item_id)

    def load_in(self, tx, release_id, item_id):
        identifier(release_id, "release_version_id")
        identifier(item_id, "item_id")
        key = {"setting_key": f"shared_note_item_models.v1:{release_id}:{item_id}"}
        try:
            payload = tx.get("app_setting", key)["setting_value"]
        except LookupError:
            return ()
        require(isinstance(payload, dict) and payload.get("schema") == 1
                and payload.get("release_id") == release_id and payload.get("item_id") == item_id
                and isinstance(payload.get("entries"), list), "저장된 모델 적용 정보의 형식을 확인해 주세요.")
        entries = payload["entries"]
        marks = _marks(ModelItemMark(**{**entry["mark"], "state": ItemState(entry["mark"]["state"])}) for entry in entries)
        ids = [entry["model_id"] for entry in entries]
        for model_id in ids:
            identifier(model_id, "model_id")
        require(len(ids) == len(set(ids)), "저장된 적용 모델 ID가 중복됐습니다.")
        reason = None
        try:
            release = tx.get("release_version", {"release_version_id": release_id})
            item = tx.get("item", {"item_id": item_id})
            link = tx.get("release_version_item", {"release_version_id": release_id, "item_id": item_id})
            models = sorted((tx.get("equipment_group", {"group_id": model_id}) for model_id in ids),
                            key=lambda value: value["group_id"])
            if not _matches_item_fingerprint(payload.get("fingerprint"), {"release": release, "item": item, "models": models}):
                reason = "릴리즈·Item·모델 정보가 변경됐습니다. 적용 표식을 다시 확인해 주세요."
            elif payload.get("link_fingerprint") != _fingerprint(link):
                reason = "Item의 연결·적용 상태 확인 근거가 없거나 변경됐습니다. 적용 표식을 다시 확인해 주세요."
        except LookupError:
            reason = "릴리즈·Item·모델 연결이 없어졌습니다. 적용 표식을 다시 확인해 주세요."
        return tuple(StoredModelItem(model_id, mark.model_name, ItemState.UNKNOWN if reason else mark.state,
                                     mark, reason) for model_id, mark in zip(ids, marks))
