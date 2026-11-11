"""데이터 관리 화면용 읽기 모델과 설비군 등록 (11-10 데이터 관리 개편).

- 현황표: 가져올 때 보관한 시트 원본 셀(import_batch.raw_data)과, 그 셀에서 만든 DB 값의 연결(source_reference)을
  엑셀 표 모양으로 돌려준다. 값 수정은 기존 관리 저장(ManagementService.save)으로 한다.
- 변경 이력: 관리 저장이 남기는 MANUAL 입력 작업(수정 전 값·수정 요청)을 일시·파일·시트·셀·전후 값으로 푼다.
- 설비군 등록: equipment_group에 새 설비군(개조형이면 상위 설비군)을 넣고 같은 MANUAL 기록을 남긴다.
- 현황표 행 삭제: 고른 설비의 그 현황표 관측(PM → SW → 설비 현황)을 지우고, 지운 칸 위치를 MANUAL 기록에 남긴다 (11-11).
- Site 등록: Site 테이블이 없어 관리 설정(app_setting ``registered_sites.v1``)에 이름 목록으로 저장한다 (11-11).
  조회 선택지는 저장된 관측의 Site에 이 목록을 더해 만든다 (:func:`registered_sites`).

DB 시각(imported_at)은 DB 서버 시계 기준이라, 화면에는 이 PC 시계와의 차이를 더해 보인다 (11-11).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import text

from setron.features.equipment.repository import EquipmentRepository
from setron.features.storage.inputs import require

# 가져오기 원본(raw_data)에 함께 보관하는 엑셀 파일 이름의 키. 엑셀 시트 이름에는 '['·']'를 쓸 수 없으므로
# 시트 이름과 겹치지 않는다 (11-10 결정: 앞으로 가져올 때 파일 이름 저장).
WORKBOOK_KEY = "[파일]"
SHEET_KEY = "[현황표]"  # 데이터 관리에서 직접 만든 빈 현황표의 Site·기준일 (11-11)
CATEGORIES = ("POLY", "OXIDE", "KCTC")
SITES_KEY = "registered_sites.v1"
# 직접 만든 현황표의 열 (시연 현황표와 같은 순서)
MANUAL_HEADERS = {"A": ("No", None), "B": ("Line", "line"), "C": ("설비군", "model_name"),
                  "D": ("설비호기", "equipment_code"), "E": ("Version", "version_raw")}
ROW_FIELDS = ("line", "model_name", "equipment_code", "version_raw")
SNAPSHOT_TEXT = frozenset({"line", "model_name", "production_area", "structure_type", "geometry_type",
                          "modification_type"})
FIELD_LABELS = {
    "equipment_code": "호기", "site": "Site", "line": "Line", "base_date": "기준일", "model_name": "모델",
    "group_id": "설비군", "production_area": "생산 구분", "structure_type": "구조", "geometry_type": "형상",
    "modification_type": "개조", "version_raw": "SW Ver", "family_id": "SW 계열", "pm_name": "PM",
    "model_name_raw": "모델 원문", "released_at": "릴리즈일", "version_order": "버전 순서", "version_suffix": "접미사",
    "item_no": "Item No", "content": "Item 내용", "item_state": "적용 상태", "target_scope": "적용 범위",
    "target_group_ids": "적용 모델", "item_mapping_status": "Item 연결", "risk_reason": "위험 사유",
    "is_enabled": "사용 여부", "rule_type": "위험 유형", "setting_value": "설정값",
}
TABLE_LABELS = {
    "equipment": "설비", "equipment_snapshot": "설비 현황", "equipment_version": "설비 SW", "equipment_pm": "PM SW",
    "release_version": "릴리즈 버전", "item": "Item", "release_version_item": "버전 Item", "risk_rule": "위험 지정",
    "app_setting": "설정", "equipment_group": "설비군", "site": "Site", "status_sheet": "현황표",
}
RULE_LABELS = {"SAFETY_REQUIRED": "필수", "DEFECT_ITEM": "결함", "DIRECT_VERSION": "위험 버전"}


def with_workbook(raw, workbook_name):
    """가져오기 원본에 파일 이름을 더한다. 이름이 없으면 그대로 둔다."""
    name = (workbook_name or "").strip()
    return {**raw, WORKBOOK_KEY: name} if name else dict(raw)


def sheet_names(raw) -> tuple[str, ...]:
    """raw_data의 시트 이름들. '['로 시작하는 키(파일 이름 · 직접 만든 현황표 정보)는 시트가 아니다.

    엑셀 시트 이름에는 '['·']'를 쓸 수 없으므로 실제 시트와 겹치지 않는다.
    """
    return tuple(str(k) for k in (raw or {}) if not str(k).startswith("["))


def _workbook(raw):
    value = (raw or {}).get(WORKBOOK_KEY) if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value else None


def _raw(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)


def _row_number(address):
    match = re.match(r"[A-Za-z]+(\d+)$", address or "")
    return int(match.group(1)) if match else None


def _locations(row, name):
    value = (row.get("source_reference") or {}).get(name)
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def _cell_order(address):
    column, row = re.match(r"([A-Za-z]*)(\d*)", address or "").groups()
    return int(row or 0), len(column), column


def registered_sites(tx) -> tuple[str, ...]:
    """데이터 관리에서 등록한 Site 이름들 (관리 설정). 없거나 형식이 다르면 빈 목록."""
    try:
        value = tx.get("app_setting", {"setting_key": SITES_KEY})["setting_value"]
    except LookupError:
        return ()
    names = value.get("sites") if isinstance(value, dict) else None
    return tuple(str(n) for n in names if isinstance(n, str) and n.strip()) if isinstance(names, list) else ()


def clock_offset(storage) -> timedelta:
    """이 PC 시계 - DB 서버 시계 (15분 단위). DB가 UTC로 기록해도 화면은 PC 시각으로 보인다. 못 읽으면 0."""
    try:
        with storage.transaction() as tx:
            now = tx.session.execute(text("SELECT NOW()")).scalar()
    except Exception:  # noqa: BLE001 — SQLite 시험 저장소 등 NOW()가 없으면 보정하지 않는다
        return timedelta(0)
    if not isinstance(now, datetime):
        return timedelta(0)
    return timedelta(minutes=round((datetime.now() - now).total_seconds() / 900) * 15)


def _shift(value, offset):
    return value + offset if isinstance(value, datetime) else value


def _plain(row):
    """기록(JSON)에 넣을 수 있게 날짜는 YYYY-MM-DD, 바이트는 글자로 바꾼 행."""
    return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v.decode("utf-8", "replace")
                if isinstance(v, bytes) else v) for k, v in row.items()}


@dataclass(frozen=True)
class StatusSheet:
    batch_id: int
    base_date: date
    site: str
    sheet: str
    workbook: str | None
    imported_at: datetime | None
    equipment_count: int

    @property
    def label(self):
        return f"{self.base_date} · {self.site} · {self.sheet}"


@dataclass(frozen=True)
class CellBinding:
    """현황표 한 칸과 DB 값의 연결. editable이면 table·key·field로 관리 저장을 한다."""

    table: str
    key: dict
    field: str
    current: str
    editable: bool
    label: str


@dataclass(frozen=True)
class SheetGrid:
    batch_id: int
    sheet: str
    workbook: str | None
    cells: dict  # 주소 → 가져올 때의 원본 값
    bindings: dict  # 주소 → CellBinding (DB 값과 연결된 칸만)
    owners: dict = field(default_factory=dict)  # 주소 → 그 칸을 만든 설비 현황(snapshot_id). 행 삭제에 쓴다
    deleted: dict = field(default_factory=dict)  # 주소 → 안내 (데이터 관리에서 지운 행의 칸)
    columns: dict = field(default_factory=dict)  # 열 글자 → 행 추가 때 적는 값 (line · model_name · equipment_code · version_raw)
    groups: tuple = ()  # 등록된 설비군 이름 (행 추가 때 새 설비군인지 판단)


@dataclass(frozen=True)
class GroupRow:
    group_id: int
    name: str
    category: str
    parent_id: int | None
    parent_name: str | None
    equipment_count: int


@dataclass(frozen=True)
class EquipmentRow:
    equipment_id: int
    code: str
    group_ids: tuple[int, ...]
    group_names: str
    model_name: str
    site: str
    line: str
    base_date: date | None
    snapshot_count: int


@dataclass(frozen=True)
class ReleaseFamily:
    family_id: int
    name: str
    group_id: int | None
    version_count: int
    variants: tuple[tuple[int, str], ...]  # 개조형(하위 설비군) (ID, 이름)


@dataclass(frozen=True)
class ReleaseRow:
    """릴리즈노트 표 한 줄: 버전 하나 또는 그 버전의 Item 하나."""

    release_version_id: int
    version_raw: str
    released_at: str
    item_id: int | None
    item_no: str
    content: str
    applied_models: str
    target_group_ids: tuple[int, ...]
    risk: str | None
    row_number: int | None
    first_of_version: bool


@dataclass(frozen=True)
class HistoryEntry:
    batch_id: int
    at: datetime | None
    kind: str  # 수정 · 등록
    table: str
    key: dict
    target: str
    field: str
    before: str
    after: str
    workbook: str | None
    sheet: str | None
    cell: str | None

    @property
    def place(self):
        parts = [self.workbook or "파일 이름 없음"]
        if self.sheet:
            parts.append(self.sheet + (f" · {self.cell}" if self.cell else ""))
        return " · ".join(parts)


class DataAdminService:
    """화면 워커에서 부르는 동기 읽기·등록 경계."""

    def __init__(self, storage):
        self.storage = storage
        self.rows = EquipmentRepository()

    # --- 현황표 ---------------------------------------------------------------------
    def status_sheets(self) -> tuple[StatusSheet, ...]:
        offset = clock_offset(self.storage)
        with self.storage.transaction() as tx:
            snapshots = self.rows.rows(tx, "equipment_snapshot")
            batches = {b["batch_id"]: b for b in self.rows.rows(tx, "import_batch")}
        counts = Counter((s["batch_id"], s["base_date"], _text(s["site"])) for s in snapshots if s.get("batch_id"))
        # 직접 만든 현황표는 설비가 아직 없어도 목록에 보인다
        for batch_id, batch in batches.items():
            meta = _raw(batch.get("raw_data")).get(SHEET_KEY)
            if isinstance(meta, dict) and not any(key[0] == batch_id for key in counts):
                counts[(batch_id, date.fromisoformat(meta["base_date"]), meta["site"])] = 0
        result = []
        for (batch_id, base_date, site), count in counts.items():
            batch = batches.get(batch_id) or {}
            raw = _raw(batch.get("raw_data"))
            names = sheet_names(raw) or ("시트 미확인",)
            result.append(StatusSheet(batch_id, base_date, site, ", ".join(names), _workbook(raw),
                                      _shift(batch.get("imported_at"), offset), count))
        return tuple(sorted(result, key=lambda s: (s.base_date, s.site, s.batch_id), reverse=True))

    def sheet_grid(self, batch_id) -> SheetGrid:
        with self.storage.transaction() as tx:
            batch = tx.get("import_batch", {"batch_id": batch_id})
            raw = _raw(batch.get("raw_data"))
            names = sheet_names(raw)
            require(bool(names), "이 입력 작업에는 원본 시트가 없습니다.")
            sheet = names[0]
            cells = dict(raw.get(sheet) or {})
            bindings, owners = {}, {}
            equipment = {e["equipment_id"]: e for e in self.rows.rows(tx, "equipment")}
            for snapshot in self.rows.rows(tx, "equipment_snapshot", batch_id=batch_id):
                code = _text(equipment.get(snapshot["equipment_id"], {}).get("equipment_code"))
                sid = {"snapshot_id": snapshot["snapshot_id"]}
                bound = {}
                for name in (snapshot.get("source_reference") or {}):
                    if name == "equipment_code":
                        target = ("equipment", {"equipment_id": snapshot["equipment_id"]}, code, True)
                    else:
                        target = ("equipment_snapshot", sid, _text(snapshot.get(name)), name in SNAPSHOT_TEXT)
                    self._bind(bound, snapshot, name, batch_id, sheet, code, *target)
                versions = self.rows.rows(tx, "equipment_version", snapshot_id=snapshot["snapshot_id"])
                pms = self.rows.rows(tx, "equipment_pm", snapshot_id=snapshot["snapshot_id"])
                with_pm = {pm["equipment_version_id"] for pm in pms}
                for version in versions:
                    key = {"equipment_version_id": version["equipment_version_id"]}
                    for name in (version.get("source_reference") or {}):
                        editable = name == "version_raw" and version["equipment_version_id"] not in with_pm
                        self._bind(bound, version, name, batch_id, sheet, code, "equipment_version", key,
                                   _text(version.get(name)), editable)
                for pm in pms:
                    for name in (pm.get("source_reference") or {}):
                        self._bind(bound, pm, name, batch_id, sheet, code, "equipment_pm", {"pm_id": pm["pm_id"]},
                                   _text(pm.get(name)), name == "version_raw")
                bindings.update(bound)
                owners.update(dict.fromkeys(bound, snapshot["snapshot_id"]))
            deleted = self._deleted_cells(tx, batch_id, sheet)
            columns = self._columns(raw, bindings)
            groups = tuple(sorted(_text(g["group_name"]) for g in self.rows.rows(tx, "equipment_group")))
        return SheetGrid(batch_id, sheet, _workbook(raw), cells, bindings, owners,
                         {a: v for a, v in deleted.items() if a not in bindings}, columns, groups)

    @staticmethod
    def _columns(raw, bindings):
        """행 추가 때 열마다 적을 값. 직접 만든 현황표는 정해진 열, 가져온 시트는 이미 연결된 칸의 열에서 정한다."""
        if isinstance(raw.get(SHEET_KEY), dict):
            return {letter: name for letter, (_, name) in MANUAL_HEADERS.items() if name}
        found = {}
        for address, binding in sorted(bindings.items(), key=lambda pair: _cell_order(pair[0])):
            letter = re.match(r"[A-Za-z]+", address).group(0).upper()
            if binding.field in ROW_FIELDS and binding.table != "equipment_pm" and binding.field not in found.values():
                found.setdefault(letter, binding.field)
        return found if {"equipment_code", "model_name", "version_raw"} <= set(found.values()) else {}

    @staticmethod
    def _bind(bindings, row, name, batch_id, sheet, code, table, key, current, editable):
        if name not in row and name != "equipment_code":
            return  # DB 칸이 없는 출처(예: 가져올 때 남긴 설비군 이름)는 원본 값을 그대로 보인다
        for location in _locations(row, name):
            if location.get("sheet") != sheet or location.get("batch_id", row.get("batch_id")) != batch_id:
                continue
            for address in location.get("cells") or ():
                bindings[address] = CellBinding(table, dict(key), name, current, editable,
                                                f"{code} · {FIELD_LABELS.get(name, name)}")

    def _journals(self, tx, key):
        """MANUAL 기록 중 ``key``(management_delete 등)가 있는 것 [(batch_id, 내용)]."""
        found = []
        for batch in self.rows.rows(tx, "import_batch", source_type="MANUAL"):
            value = _raw(batch.get("raw_data")).get(key)
            if isinstance(value, dict):
                found.append((batch["batch_id"], value))
        return found

    def _deleted_cells(self, tx, batch_id, sheet):
        """데이터 관리에서 지운 행의 칸 → '삭제됨 · 호기'. 되살린 삭제와 '추가 취소'는 빼고 행 삭제 기록에서 읽는다."""
        restored = {value.get("batch_id") for _, value in self._journals(tx, "management_restore")}
        found = {}
        for journal, removed in self._journals(tx, "management_delete"):
            if (removed.get("batch_id") != batch_id or removed.get("sheet") != sheet or journal in restored
                    or removed.get("undo_add")):
                continue
            for row in removed.get("rows") or ():
                for address in row.get("cells") or ():
                    found[address] = f"삭제됨 · {row.get('equipment_code') or '설비'}"
        return found

    def delete_rows(self, batch_id, snapshot_ids, *, undo_add=False) -> int:
        """현황표의 고른 설비 행을 지운다: 그 관측의 PM → SW → 설비 현황 순서로 한 트랜잭션에서 지운다.

        설비 호기(equipment)와 가져오기 원본(import_batch)은 남긴다 (다른 기준일 관측·감사 기록). 지운 칸 위치·값과
        되살릴 때 쓸 행 전체는 MANUAL 기록(management_delete)으로 남겨 현황표·변경 이력에 보인다.
        ``undo_add``는 방금 추가한 행의 되돌리기라 '삭제됨' 표시를 남기지 않는다.

        return
        - 삭제 기록의 입력 작업 ID (되돌리기에서 :meth:`restore_rows`로 되살린다).
        """
        with self.storage.transaction() as tx:
            return self._delete_rows_in(tx, batch_id, snapshot_ids, undo_add=undo_add)

    def _delete_rows_in(self, tx, batch_id, snapshot_ids, *, undo_add=False):
        ids = tuple(dict.fromkeys(int(i) for i in snapshot_ids))
        require(bool(ids), "지울 행을 골라 주세요.")
        batch = tx.get("import_batch", {"batch_id": batch_id})
        sheet = (sheet_names(_raw(batch.get("raw_data"))) or ("",))[0]
        equipment = {e["equipment_id"]: _text(e["equipment_code"]) for e in self.rows.rows(tx, "equipment")}
        removed = []
        for sid in ids:
            found = self.rows.rows(tx, "equipment_snapshot", snapshot_id=sid)
            require(bool(found) and found[0].get("batch_id") == batch_id,
                    "이미 지웠거나 이 현황표의 행이 아닙니다. 목록을 다시 읽어 주세요.")
            snapshot = found[0]
            versions = self.rows.rows(tx, "equipment_version", snapshot_id=sid)
            pms = self.rows.rows(tx, "equipment_pm", snapshot_id=sid)
            cells = {}
            for row in (snapshot, *versions, *pms):
                for name in (row.get("source_reference") or {}):
                    if name not in row and name != "equipment_code":
                        continue  # 현황표에 보이지 않는 출처 칸(예: 설비군 이름)은 삭제 표시에서 뺀다
                    for location in _locations(row, name):
                        if location.get("sheet") == sheet and location.get("batch_id", row.get("batch_id")) == batch_id:
                            cells.update(dict.fromkeys(location.get("cells") or ()))
            code = equipment.get(snapshot["equipment_id"], "")
            for pm in pms:
                tx.repository.delete(tx.session, "equipment_pm", {"pm_id": pm["pm_id"]})
            for version in versions:
                tx.repository.delete(tx.session, "equipment_version", {"equipment_version_id": version["equipment_version_id"]})
            tx.repository.delete(tx.session, "equipment_snapshot", {"snapshot_id": sid})
            removed.append({"snapshot_id": sid, "equipment_code": code, "base_date": _text(snapshot.get("base_date")),
                            "site": _text(snapshot.get("site")), "line": _text(snapshot.get("line")),
                            "versions": [_text(v.get("version_raw")) for v in versions],
                            "cells": sorted(cells, key=_cell_order),
                            "data": {"snapshot": _plain(snapshot), "versions": [_plain(v) for v in versions],
                                     "pms": [_plain(p) for p in pms]}})
        journal = tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_delete": {
            "table": "equipment_snapshot", "batch_id": batch_id, "sheet": sheet, "rows": removed,
            **({"undo_add": True} if undo_add else {})}}})
        return journal["batch_id"]

    def restore_rows(self, journal_id) -> int:
        """행 삭제 기록 하나를 되살린다 (되돌리기). 지운 설비 현황 · SW · PM을 같은 값으로 다시 넣는다.

        같은 설비의 같은 기준일 관측이 그사이 다시 생겼거나 이미 되살렸으면 거부한다.
        """
        with self.storage.transaction() as tx:
            return self._restore_rows_in(tx, journal_id)

    def _restore_rows_in(self, tx, journal_id):
        removed = _raw(tx.get("import_batch", {"batch_id": journal_id}).get("raw_data")).get("management_delete")
        require(isinstance(removed, dict), "되살릴 삭제 기록이 아닙니다.")
        require(all(value.get("batch_id") != journal_id for _, value in self._journals(tx, "management_restore")),
                "이미 되살린 삭제입니다.")
        count = 0
        for row in removed.get("rows") or ():
            data = row.get("data") or {}
            snapshot = dict(data.get("snapshot") or {})
            require(bool(snapshot), "이 삭제 기록에는 되살릴 자료가 없습니다.")
            snapshot.pop("snapshot_id", None)
            snapshot["base_date"] = date.fromisoformat(snapshot["base_date"])
            require(not self.rows.rows(tx, "equipment_snapshot", equipment_id=snapshot["equipment_id"],
                                       base_date=snapshot["base_date"]),
                    f"{row.get('equipment_code')}의 같은 기준일 현황이 이미 있어 되살릴 수 없습니다.")
            created = tx.insert("equipment_snapshot", snapshot)
            version_ids = {}
            for version in data.get("versions") or ():
                values = {k: v for k, v in version.items() if k != "equipment_version_id"}
                values["snapshot_id"] = created["snapshot_id"]
                version_ids[version["equipment_version_id"]] = tx.insert("equipment_version", values)["equipment_version_id"]
            for pm in data.get("pms") or ():
                values = {k: v for k, v in pm.items() if k != "pm_id"}
                values["snapshot_id"] = created["snapshot_id"]
                values["equipment_version_id"] = version_ids.get(pm.get("equipment_version_id"))
                tx.insert("equipment_pm", values)
            count += 1
        tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_restore": {
            "batch_id": journal_id, "sheet": removed.get("sheet"), "source": removed.get("batch_id"),
            "codes": [row.get("equipment_code") for row in removed.get("rows") or ()],
            "undo_add": bool(removed.get("undo_add"))}}})
        return count

    # --- 현황표 행 추가 · 빈 현황표 (11-11) ------------------------------------------------
    def _sheet_place(self, tx, batch_id):
        """현황표(입력 작업)의 시트 이름 · Site · 기준일 · 원본."""
        raw = _raw(tx.get("import_batch", {"batch_id": batch_id}).get("raw_data"))
        names = sheet_names(raw)
        require(bool(names), "이 입력 작업에는 원본 시트가 없습니다.")
        meta = raw.get(SHEET_KEY)
        if isinstance(meta, dict):
            return names[0], meta["site"], date.fromisoformat(meta["base_date"]), raw
        snapshots = self.rows.rows(tx, "equipment_snapshot", batch_id=batch_id)
        require(bool(snapshots), "이 현황표의 Site · 기준일을 알 수 없습니다. 데이터 가져오기로 다시 넣어 주세요.")
        return names[0], _text(snapshots[0]["site"]), snapshots[0]["base_date"], raw

    def _family_for(self, tx, batch_id, group, line):
        """새 행의 SW 계열: 같은 현황표 · 같은 설비군 설비가 쓰는 계열 → 설비군(또는 상위 설비군)의 계열 하나.

        계열이 하나도 없는 새 설비군이면 설비군 이름으로 계열을 만든다 (릴리즈 노트가 없어 '정보 부족'으로 보인다).
        """
        peers = [s for s in self.rows.rows(tx, "equipment_snapshot", batch_id=batch_id) if s.get("group_id") == group["group_id"]]
        used = Counter(v["family_id"] for s in peers for v in self.rows.rows(tx, "equipment_version", snapshot_id=s["snapshot_id"]))
        if used:
            return used.most_common(1)[0][0]
        owners = [group["group_id"]] + ([group["parent_group_id"]] if group.get("parent_group_id") else [])
        families = [f for owner in owners for f in self.rows.rows(tx, "version_family", group_id=owner)]
        if not families:
            return tx.insert("version_family", {"group_id": group["group_id"], "family_name": _text(group["group_name"])[:50],
                                                "ui_type": None})["family_id"]
        areas = {s.get("production_area") for s in self.rows.rows(tx, "equipment_snapshot", batch_id=batch_id)
                 if _text(s.get("line")) == line and s.get("production_area")}
        foundry = areas == {"FOUNDRY"}
        families = [f for f in families if ("(파운드리)" in _text(f["family_name"])) == foundry] or families
        if len(families) > 1:
            integrated = [f for f in families if f.get("ui_type") == "INTEGRATED"]  # UI 표기가 없으면 통합UI (멘토 10-28)
            families = integrated or families
        require(len(families) == 1, f"설비군 '{_text(group['group_name'])}'의 SW 계열을 하나로 정할 수 없습니다. "
                                    "같은 설비군 설비가 있는 현황표에서 추가하거나 데이터 가져오기로 넣어 주세요.")
        return families[0]["family_id"]

    def add_row(self, batch_id, values, row_number) -> int:
        """현황표에 설비 한 행을 더한다 (호기 · 설비군(모델) · SW 버전 필수, Line 선택).

        Site · 기준일은 현황표에서 정한다. 호기가 처음이면 설비를 만들고, 같은 기준일 관측이 이미 있으면 거부한다.
        원본 셀은 바꾸지 않고, 새 칸 위치(행 ``row_number``)를 출처로 남긴다.

        return
        - 새 설비 현황 ID.
        """
        with self.storage.transaction() as tx:
            return self._add_row_in(tx, batch_id, values, row_number)

    def _add_row_in(self, tx, batch_id, values, row_number):
        values = {k: (v or "").strip() for k, v in values.items()}
        code, model, version, line = (values.get(k, "") for k in ("equipment_code", "model_name", "version_raw", "line"))
        require(bool(code) and bool(model) and bool(version), "호기 · 설비군 · SW 버전을 모두 적어 주세요.")
        sheet, site, base_date, raw = self._sheet_place(tx, batch_id)
        columns = {name: letter for letter, name in self._columns(raw, self._bound_fields(tx, batch_id, sheet)).items()}
        group = next((g for g in self.rows.rows(tx, "equipment_group")
                      if _text(g["group_name"]).casefold() == model.casefold()), None)
        require(group is not None, f"설비군 '{model}'이(가) 없습니다. 먼저 등록해 주세요.")
        found = next((e for e in self.rows.rows(tx, "equipment") if _text(e["equipment_code"]) == code), None)
        equipment = found or tx.insert("equipment", {"equipment_code": code})
        require(not self.rows.rows(tx, "equipment_snapshot", equipment_id=equipment["equipment_id"], base_date=base_date),
                f"'{code}'은(는) 이 기준일 현황에 이미 있습니다.")
        family = self._family_for(tx, batch_id, group, line)
        release = next((r["release_version_id"] for r in self.rows.rows(tx, "release_version", family_id=family)
                        if _text(r["version_raw"]) == version), None)
        refs = {name: {"sheet": sheet, "cells": [f"{columns[name]}{row_number}"]} for name in ROW_FIELDS if name in columns}
        areas = {s.get("production_area") for s in self.rows.rows(tx, "equipment_snapshot", batch_id=batch_id)
                 if _text(s.get("line")) == line and s.get("production_area")}
        snapshot = tx.insert("equipment_snapshot", {
            "equipment_id": equipment["equipment_id"], "base_date": base_date, "site": site, "line": line or None,
            "model_name": _text(group["group_name"]), "group_id": group["group_id"],
            "production_area": next(iter(areas)) if len(areas) == 1 else None, "batch_id": batch_id,
            "source_reference": {k: v for k, v in refs.items() if k != "version_raw"}})
        tx.insert("equipment_version", {"snapshot_id": snapshot["snapshot_id"], "family_id": family, "version_raw": version,
                                        "release_version_id": release, "batch_id": batch_id,
                                        "source_reference": {k: v for k, v in refs.items() if k == "version_raw"}})
        tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_create": {
            "table": "equipment_snapshot", "values": {
                "snapshot_id": snapshot["snapshot_id"], "label": code, "batch_id": batch_id, "sheet": sheet,
                "cells": sorted((c for ref in refs.values() for c in ref["cells"]), key=_cell_order),
                "summary": " · ".join(v for v in (site, line, base_date.isoformat(), _text(group["group_name"]), version) if v)}}}})
        return snapshot["snapshot_id"]

    def apply_rows(self, batch_id, adds=(), deletes=()) -> dict:
        """현황표 [저장]의 행 추가 · 삭제를 한 트랜잭션으로 반영한다 (11-11 4차: 행 추가 · 삭제도 저장할 때 한꺼번에).

        param
        - adds: [(값 dict, 새 행 번호)] — :meth:`add_row`와 같은 값.
        - deletes: 지울 설비 현황 ID 목록.

        return
        - {"added": [새 설비 현황 ID], "journal": 삭제 기록 ID 또는 None}
        """
        with self.storage.transaction() as tx:
            journal = self._delete_rows_in(tx, batch_id, deletes) if deletes else None
            added = [self._add_row_in(tx, batch_id, values, row) for values, row in adds]
        return {"added": added, "journal": journal}

    def undo_rows(self, batch_id, added=(), journal=None) -> None:
        """[저장]으로 반영한 행 추가 · 삭제를 한 트랜잭션으로 되돌린다 (추가한 행 지우기 · 지운 행 되살리기)."""
        with self.storage.transaction() as tx:
            if added:
                self._delete_rows_in(tx, batch_id, added, undo_add=True)
            if journal is not None:
                self._restore_rows_in(tx, journal)

    def _bound_fields(self, tx, batch_id, sheet):
        """열 위치를 정하려고 쓰는 가벼운 연결 정보 (주소 → CellBinding)."""
        bindings = {}
        for snapshot in self.rows.rows(tx, "equipment_snapshot", batch_id=batch_id):
            self._bind(bindings, snapshot, "equipment_code", batch_id, sheet, "", "equipment", {}, "", True)
            for name in ("line", "model_name"):
                self._bind(bindings, snapshot, name, batch_id, sheet, "", "equipment_snapshot", {}, "", True)
            for version in self.rows.rows(tx, "equipment_version", snapshot_id=snapshot["snapshot_id"]):
                self._bind(bindings, version, "version_raw", batch_id, sheet, "", "equipment_version", {}, "", True)
        return bindings

    def create_sheet(self, site, base_date) -> int:
        """Site · 기준일의 빈 현황표(머리글만)를 만든다. 같은 Site · 기준일 현황표가 이미 있으면 거부한다.

        return
        - 새 현황표의 입력 작업 ID.
        """
        site = (site or "").strip()
        require(bool(site), "Site를 골라 주세요.")
        require(isinstance(base_date, date), "기준일을 YYYY-MM-DD로 적어 주세요.")
        require(all(not (s.base_date == base_date and s.site == site) for s in self.status_sheets()),
                f"{site} · {base_date} 현황표가 이미 있습니다. 그 현황표에 행을 추가해 주세요.")
        sheet = f"{site} {base_date.isoformat()} (직접 입력)"
        with self.storage.transaction() as tx:
            created = tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {
                sheet: {f"{letter}1": text for letter, (text, _) in MANUAL_HEADERS.items()},
                SHEET_KEY: {"site": site, "base_date": base_date.isoformat()}}})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_create": {
                "table": "status_sheet", "values": {"label": f"{site} · {base_date.isoformat()}", "batch_id": created["batch_id"]}}}})
        return created["batch_id"]

    # --- 릴리즈노트 추가 (11-11) -----------------------------------------------------------------
    def add_release_version(self, family_id, version_raw, released_at=None) -> int:
        """계열에 새 버전을 더한다. 순서는 맨 끝(기존 최대 + 10)이고, 같은 원문의 설비 SW 중 연결이 없는 것은 이 버전에 잇는다."""
        from setron.features.release.schemas import ReleaseInput
        from setron.features.release.service import ReleaseService

        version_raw = (version_raw or "").strip()
        require(bool(version_raw), "버전명을 적어 주세요.")
        with self.storage.transaction() as tx:
            current = self.rows.rows(tx, "release_version", family_id=family_id)
            require(all(_text(r["version_raw"]) != version_raw for r in current), f"'{version_raw}'은(는) 이미 있는 버전입니다.")
            orders = [r["version_order"] for r in current if r.get("version_order") is not None]
            detail = ReleaseService(self.storage).register_release_in(tx, ReleaseInput(
                family_id=family_id, version_raw=version_raw, released_at=released_at,
                version_order=max(orders) + 10 if orders else None))
            release_id = detail.release.current["release_version_id"]
            for snapshot_version in self.rows.rows(tx, "equipment_version", family_id=family_id):
                if snapshot_version.get("release_version_id") is None and _text(snapshot_version["version_raw"]) == version_raw:
                    tx.update("equipment_version", {"equipment_version_id": snapshot_version["equipment_version_id"]},
                              {"release_version_id": release_id})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_create": {
                "table": "release_version", "values": {"label": version_raw, "release_version_id": release_id}}}})
        return release_id

    def add_release_item(self, release_version_id, content, item_no=None, group_ids=None) -> int:
        """버전에 새 Item을 더한다. 적용 모델을 적지 않으면 '전체 설비'로 둔다 (11-11 팀 결정과 같은 기본값)."""
        from setron.features.release.schemas import ItemInput, ItemLinkInput
        from setron.features.item.schemas import ItemState, TargetScope
        from setron.features.release.service import ReleaseService

        content = (content or "").strip()
        require(bool(content), "Item 내용을 적어 주세요.")
        if item_no is None:  # 번호는 적지 않고 그 버전의 맨 뒤 번호 다음 (순서는 끌어서 바꾼다)
            with self.storage.transaction() as tx:
                numbers = [tx.get("item", {"item_id": link["item_id"]}).get("item_no") for link in
                           self.rows.rows(tx, "release_version_item", release_version_id=release_version_id)]
            item_no = max((n for n in numbers if isinstance(n, int)), default=0) + 1
        service = ReleaseService(self.storage)
        item = service.register_item(release_version_id, ItemInput(content=content, item_no=item_no))
        item_id = item.current["item_id"]
        ids = tuple(group_ids or ())
        service.save_item_links(release_version_id, (ItemLinkInput(
            item_id=item_id, item_state=ItemState.APPLIED, target_scope=TargetScope.LISTED if ids else TargetScope.ALL,
            target_group_ids=ids),))
        with self.storage.transaction() as tx:
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_create": {
                "table": "item", "values": {"label": content[:40], "item_id": item_id,
                                            "release_version_id": release_version_id}}}})
        return item_id

    def delete_release_rows(self, release_version_id, item_id=None) -> str:
        """릴리즈노트 줄 삭제. ``item_id``가 있으면 그 버전에서 Item 하나를, 없으면 버전과 그 Item 연결을 모두 지운다.

        다른 버전에 연결되지 않은 Item은 함께 지운다. 위험 지정이 가리키던 버전 · Item은 근거를 남기고 '연결 미확인'으로
        바꾸며(StorageTransaction.delete_release_*), 설비 SW의 릴리즈 연결은 DB 규칙대로 비워진다.

        return
        - 지운 줄 이름 (안내용).
        """
        with self.storage.transaction() as tx:
            release = tx.get("release_version", {"release_version_id": release_version_id})
            links = self.rows.rows(tx, "release_version_item", release_version_id=release_version_id)
            targets = [link for link in links if item_id is None or link["item_id"] == item_id]
            require(bool(targets) or item_id is None, "이미 지운 Item입니다. 다시 읽어 주세요.")
            removed = []
            for link in targets:
                item = tx.get("item", {"item_id": link["item_id"]})
                removed.append({"item_id": link["item_id"], "item_no": item.get("item_no"), "content": _text(item.get("content"))})
                tx.repository.delete(tx.session, "release_version_item",
                                     {"release_version_id": release_version_id, "item_id": link["item_id"]})
                if not self.rows.rows(tx, "release_version_item", item_id=link["item_id"]):
                    tx.delete_release_item(link["item_id"])
            if item_id is None:
                tx.delete_release_version(release_version_id)
            label = _text(release["version_raw"]) + (f" · Item {removed[0]['content'][:30]}" if item_id is not None else "")
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_delete_release": {
                "release_version_id": release_version_id, "family_id": release["family_id"], "version_raw": _text(release["version_raw"]),
                "item_id": item_id, "items": removed, "label": label}}})
        return label

    def reorder_items(self, release_version_id, item_ids) -> None:
        """버전 안 Item 순서를 바꾼다 (끌어서 놓기). 주어진 순서대로 Item 번호를 1부터 다시 매긴다."""
        ids = [int(i) for i in item_ids]
        with self.storage.transaction() as tx:
            links = {link["item_id"] for link in self.rows.rows(tx, "release_version_item", release_version_id=release_version_id)}
            require(set(ids) == links and len(ids) == len(links), "Item 목록이 바뀌었습니다. 다시 읽어 주세요.")
            before = []
            for number, item_id in enumerate(ids, 1):
                item = tx.get("item", {"item_id": item_id})
                before.append((item.get("item_no"), _text(item.get("content"))[:20]))
                if item.get("item_no") != number:
                    tx.update("item", {"item_id": item_id}, {"item_no": number})
            release = tx.get("release_version", {"release_version_id": release_version_id})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_reorder": {
                "release_version_id": release_version_id, "version_raw": _text(release["version_raw"]),
                "before": [f"{n}. {c}" for n, c in sorted(before, key=lambda pair: (pair[0] is None, pair[0] or 0))],
                "after": [f"{i}. {c}" for i, (_, c) in enumerate(before, 1)]}}})

    # --- 설비·설비군 -------------------------------------------------------------------
    def equipment_overview(self) -> tuple[tuple[GroupRow, ...], tuple[EquipmentRow, ...]]:
        with self.storage.transaction() as tx:
            groups = self.rows.rows(tx, "equipment_group")
            equipment = self.rows.rows(tx, "equipment")
            snapshots = self.rows.rows(tx, "equipment_snapshot")
            pms = self.rows.rows(tx, "equipment_pm")
        names = {g["group_id"]: _text(g["group_name"]) for g in groups}
        latest, counts = {}, Counter()
        for s in snapshots:
            counts[s["equipment_id"]] += 1
            if s["equipment_id"] not in latest or s["base_date"] > latest[s["equipment_id"]]["base_date"]:
                latest[s["equipment_id"]] = s
        pm_groups = defaultdict(set)
        for pm in pms:
            if pm.get("group_id"):
                pm_groups[pm["snapshot_id"]].add(pm["group_id"])
        rows, per_group = [], Counter()
        for e in equipment:
            s = latest.get(e["equipment_id"], {})
            ids = tuple(sorted(({s["group_id"]} if s.get("group_id") else set()) | pm_groups.get(s.get("snapshot_id"), set())))
            for gid in ids:
                per_group[gid] += 1
            rows.append(EquipmentRow(e["equipment_id"], _text(e["equipment_code"]), ids,
                                     ", ".join(names.get(i, "?") for i in ids) or "미지정", _text(s.get("model_name")),
                                     _text(s.get("site")), _text(s.get("line")), s.get("base_date"), counts[e["equipment_id"]]))
        group_rows = tuple(GroupRow(g["group_id"], names[g["group_id"]], _text(g["category"]), g.get("parent_group_id"),
                                    names.get(g.get("parent_group_id")), per_group[g["group_id"]]) for g in groups)
        return group_rows, tuple(sorted(rows, key=lambda r: r.code))

    def create_group(self, name, category, parent_id=None) -> GroupRow:
        """새 설비군(개조형이면 상위 설비군 지정)을 등록한다. 같은 이름은 거부한다."""
        name = (name or "").strip()
        require(bool(name) and len(name) <= 50, "설비군 이름을 50자 이내로 입력해 주세요.")
        require(category in CATEGORIES, "분류는 POLY · OXIDE · KCTC 중 하나여야 합니다.")
        with self.storage.transaction() as tx:
            groups = self.rows.rows(tx, "equipment_group")
            require(all(_text(g["group_name"]).casefold() != name.casefold() for g in groups), "이미 있는 설비군 이름입니다.")
            parent = None
            if parent_id is not None:
                parent = next((g for g in groups if g["group_id"] == parent_id), None)
                require(parent is not None, "상위 설비군을 찾지 못했습니다.")
                require(_text(parent["category"]) == category, "개조형은 상위 설비군과 같은 분류여야 합니다.")
            created = tx.insert("equipment_group", {"group_name": name, "category": category,
                                                    "parent_group_id": parent_id, "analysis_enabled": True})
            journal = {"schema": 1, "management_create": {"table": "equipment_group", "values": {
                "group_id": created["group_id"], "group_name": name, "category": category, "parent_group_id": parent_id}}}
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": journal})
        return GroupRow(created["group_id"], name, category, parent_id,
                        _text(parent["group_name"]) if parent else None, 0)

    # --- 위치(Site) ----------------------------------------------------------------------
    def sites(self) -> tuple[str, ...]:
        """데이터 관리에서 등록한 Site (관측이 없어도 조회 선택지에 보인다)."""
        with self.storage.transaction() as tx:
            return registered_sites(tx)

    def add_site(self, name) -> str:
        """Site를 관리 설정에 등록한다. 관측된 Site나 이미 등록한 이름은 거부한다."""
        name = (name or "").strip()
        require(bool(name) and len(name) <= 50, "Site 이름을 50자 이내로 입력해 주세요.")
        with self.storage.transaction() as tx:
            current = registered_sites(tx)
            known = {s.casefold() for s in current} | {
                _text(s["site"]).casefold() for s in self.rows.rows(tx, "equipment_snapshot") if s.get("site")}
            require(name.casefold() not in known, "이미 있는 Site입니다.")
            key, value = {"setting_key": SITES_KEY}, {"sites": [*current, name]}
            try:
                tx.get("app_setting", key)
            except LookupError:
                tx.insert("app_setting", {**key, "setting_value": value})
            else:
                tx.update("app_setting", key, {"setting_value": value})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {
                "schema": 1, "management_create": {"table": "site", "values": {"site": name}}}})
        return name

    # --- 릴리즈노트 --------------------------------------------------------------------
    def release_families(self) -> tuple[ReleaseFamily, ...]:
        with self.storage.transaction() as tx:
            families = self.rows.rows(tx, "version_family")
            groups = self.rows.rows(tx, "equipment_group")
            releases = self.rows.rows(tx, "release_version")
        counts = Counter(r["family_id"] for r in releases)
        children = defaultdict(list)
        for g in groups:
            if g.get("parent_group_id"):
                children[g["parent_group_id"]].append((g["group_id"], _text(g["group_name"])))
        return tuple(ReleaseFamily(f["family_id"], _text(f["family_name"]), f.get("group_id"), counts[f["family_id"]],
                                   tuple(children.get(f.get("group_id"), ()))) for f in families)

    def release_rows(self, family_id) -> tuple[ReleaseRow, ...]:
        with self.storage.transaction() as tx:
            versions = self.rows.rows(tx, "release_version", family_id=family_id)
            groups = {g["group_id"]: _text(g["group_name"]) for g in self.rows.rows(tx, "equipment_group")}
            rules = [r for r in self.rows.rows(tx, "risk_rule", family_id=family_id) if r.get("is_enabled")]
            links, items = {}, {}
            for version in versions:
                links[version["release_version_id"]] = self.rows.rows(
                    tx, "release_version_item", release_version_id=version["release_version_id"])
                for link in links[version["release_version_id"]]:
                    if link["item_id"] not in items:
                        items[link["item_id"]] = tx.get("item", {"item_id": link["item_id"]})
        item_risk = {r["item_id"]: RULE_LABELS.get(_text(r["rule_type"]), "위험") for r in rules if r.get("item_id")}
        version_risk = {r["release_version_id"]: "위험 버전" for r in rules if r.get("release_version_id")}
        # 최신 버전이 위로 (11-11 4차: 내림차순). 순서가 없는 버전은 맨 아래.
        versions.sort(key=lambda v: (v.get("version_order") is None, -(v.get("version_order") or 0), -v["release_version_id"]))
        rows = []
        for version in versions:
            vid = version["release_version_id"]
            vrow = next((_row_number(c) for loc in _locations(version, "version_raw") for c in loc.get("cells") or ()), None)
            entries = sorted(links[vid], key=lambda link: _text(items[link["item_id"]].get("item_no")).zfill(6))
            if not entries:
                rows.append(ReleaseRow(vid, _text(version["version_raw"]), _text(version.get("released_at")), None, "", "",
                                       "", (), version_risk.get(vid), vrow, True))
            for index, link in enumerate(entries):
                item = items[link["item_id"]]
                ids = tuple(link.get("target_group_ids") or ())
                scope = _text(link.get("target_scope"))
                models = ", ".join(groups.get(i, f"#{i}") for i in ids) if ids else ("전체" if scope == "ALL" else "미확인")
                irow = next((_row_number(c) for field in ("item_no", "content") for loc in _locations(item, field)
                             for c in loc.get("cells") or ()), None)
                rows.append(ReleaseRow(vid, _text(version["version_raw"]), _text(version.get("released_at")), item["item_id"],
                                       _text(item.get("item_no")), _text(item.get("content")), models, ids,
                                       item_risk.get(item["item_id"]) or version_risk.get(vid), irow, index == 0))
        return tuple(rows)

    # --- 변경 이력 ---------------------------------------------------------------------
    def history(self, limit=300) -> tuple[HistoryEntry, ...]:
        """관리 저장이 남긴 MANUAL 기록을 칸 단위 변경으로 푼다. 최신이 앞. 시각은 이 PC 시계 기준."""
        offset = clock_offset(self.storage)
        with self.storage.transaction() as tx:
            batches = self.rows.rows(tx, "import_batch")
            equipment = {e["equipment_id"]: _text(e["equipment_code"]) for e in self.rows.rows(tx, "equipment")}
            snapshots = {s["snapshot_id"]: s["equipment_id"] for s in self.rows.rows(tx, "equipment_snapshot")}
            versions = {v["equipment_version_id"]: v["snapshot_id"] for v in self.rows.rows(tx, "equipment_version")}
            releases = {r["release_version_id"]: _text(r["version_raw"]) for r in self.rows.rows(tx, "release_version")}
            items = {i["item_id"]: _text(i.get("content")) for i in self.rows.rows(tx, "item")}
        raws = {b["batch_id"]: _raw(b.get("raw_data")) for b in batches}
        imported = {b["batch_id"]: _shift(b.get("imported_at"), offset) for b in batches}
        manual = sorted((b for b in batches if _text(b.get("source_type")) == "MANUAL"),
                        key=lambda b: b["batch_id"], reverse=True)

        def target(table, key, current):
            if table == "equipment":
                return equipment.get(key.get("equipment_id"), _text(current.get("equipment_code")))
            if table == "equipment_snapshot":
                return equipment.get(snapshots.get(key.get("snapshot_id")), "설비")
            if table in ("equipment_version", "equipment_pm"):
                sid = current.get("snapshot_id") or versions.get(current.get("equipment_version_id"))
                return equipment.get(snapshots.get(sid), "설비")
            if table == "release_version":
                return releases.get(key.get("release_version_id"), _text(current.get("version_raw")))
            if table == "item":
                return (items.get(key.get("item_id")) or _text(current.get("content")))[:30]
            if table == "release_version_item":
                return f"{releases.get(key.get('release_version_id'), '버전')} · Item {key.get('item_id')}"
            return TABLE_LABELS.get(table, table)

        result = []
        for batch in manual:
            raw = raws[batch["batch_id"]]
            at = imported.get(batch["batch_id"])
            edit = raw.get("management_edit")
            if isinstance(edit, dict):
                befores = edit.get("before") or []
                for change in edit.get("changes") or []:
                    table, key = change.get("table"), change.get("key") or {}
                    before = next((b.get("current") or {} for b in befores if b.get("table") == table
                                   and all(b.get("current", {}).get(k) == v for k, v in key.items())), {})
                    for field, value in (change.get("changes") or {}).items():
                        if field == "release_version_id":
                            continue  # 버전 원문을 바꿀 때 연결을 비우는 내부 값
                        location = next(iter(_locations(before, field)), {})
                        source_batch = location.get("batch_id", before.get("batch_id"))
                        cells = location.get("cells") or []
                        result.append(HistoryEntry(
                            batch["batch_id"], at, "수정", table, dict(key), target(table, key, before),
                            FIELD_LABELS.get(field, field), _text(before.get(field)), _text(value),
                            _workbook(raws.get(source_batch)), location.get("sheet"), cells[0] if cells else None))
            created = raw.get("management_create")
            if isinstance(created, dict):
                table, values = created.get("table"), created.get("values") or {}
                if table == "equipment_snapshot":  # 현황표 행 추가
                    cells = values.get("cells") or []
                    result.append(HistoryEntry(
                        batch["batch_id"], at, "추가", table, {"snapshot_id": values.get("snapshot_id")},
                        _text(values.get("label")), "현황표 행", "", _text(values.get("summary")) or "새 행",
                        _workbook(raws.get(values.get("batch_id"))), values.get("sheet"),
                        f"{cells[0]}~{cells[-1]}" if len(cells) > 1 else (cells[0] if cells else None)))
                else:
                    name = (_text(values.get("group_name")) if table == "equipment_group" else _text(values.get("site"))
                            if table == "site" else _text(values.get("label")) if values.get("label") else
                            f"{RULE_LABELS.get(_text(values.get('rule_type')), '위험')} · {_text(values.get('risk_reason'))[:24]}")
                    result.append(HistoryEntry(batch["batch_id"], at, "등록", table, {}, name, TABLE_LABELS.get(table, table),
                                               "", "새로 등록", None, None, None))
            removed = raw.get("management_delete")
            if isinstance(removed, dict):
                source = removed.get("batch_id")
                for row in removed.get("rows") or ():
                    cells = row.get("cells") or []
                    before = " · ".join(v for v in (row.get("site"), row.get("line"), row.get("base_date"),
                                                    ", ".join(x for x in row.get("versions") or () if x)) if v)
                    result.append(HistoryEntry(
                        batch["batch_id"], at, "삭제", "equipment_snapshot", {"snapshot_id": row.get("snapshot_id")},
                        row.get("equipment_code") or "설비", "현황표 행", before,
                        "추가 취소" if removed.get("undo_add") else "삭제",
                        _workbook(raws.get(source)), removed.get("sheet"),
                        f"{cells[0]}~{cells[-1]}" if len(cells) > 1 else (cells[0] if cells else None)))
            dropped = raw.get("management_delete_release")
            if isinstance(dropped, dict):
                items = dropped.get("items") or []
                result.append(HistoryEntry(
                    batch["batch_id"], at, "삭제", "release_version_item" if dropped.get("item_id") else "release_version",
                    {}, _text(dropped.get("label")), "릴리즈 Item" if dropped.get("item_id") else "릴리즈 버전",
                    " · ".join(f"{i.get('item_no') or '-'}. {i.get('content', '')[:20]}" for i in items[:3])
                    + (f" 외 {len(items) - 3}개" if len(items) > 3 else "") or _text(dropped.get("version_raw")), "삭제",
                    None, None, None))
            reordered = raw.get("management_reorder")
            if isinstance(reordered, dict):
                result.append(HistoryEntry(
                    batch["batch_id"], at, "수정", "item", {}, _text(reordered.get("version_raw")), "Item 순서",
                    " / ".join(reordered.get("before") or []), " / ".join(reordered.get("after") or []), None, None, None))
            restored = raw.get("management_restore")
            if isinstance(restored, dict):
                result.append(HistoryEntry(
                    batch["batch_id"], at, "되돌리기", "equipment_snapshot", {}, ", ".join(restored.get("codes") or ()) or "설비",
                    "현황표 행", "삭제" if not restored.get("undo_add") else "추가 취소", "되살림",
                    _workbook(raws.get(restored.get("source"))), restored.get("sheet"), None))
            if len(result) >= limit:
                break
        return tuple(result[:limit])
