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
LINES_KEY = "registered_lines.v1"
INDEPENDENT_KEY = "independent_groups.v1"
FABS = {"MEMORY": "메모리", "FOUNDRY": "파운드리"}
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
    "app_setting": "설정", "equipment_group": "설비군", "site": "Site", "status_sheet": "현황표", "line": "라인",
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


def _setting(tx, key):
    """관리 설정 값(dict). 없거나 형식이 다르면 빈 dict."""
    try:
        value = tx.get("app_setting", {"setting_key": key})["setting_value"]
    except LookupError:
        return {}
    return value if isinstance(value, dict) else {}


def _save_setting(tx, key, value):
    try:
        tx.get("app_setting", {"setting_key": key})
    except LookupError:
        tx.insert("app_setting", {"setting_key": key, "setting_value": value})
    else:
        tx.update("app_setting", {"setting_key": key}, {"setting_value": value})


def registered_sites(tx) -> tuple[str, ...]:
    """데이터 관리에서 등록한 Site 이름들 (관리 설정). 없거나 형식이 다르면 빈 목록."""
    names = _setting(tx, SITES_KEY).get("sites")
    return tuple(str(n) for n in names if isinstance(n, str) and n.strip()) if isinstance(names, list) else ()


def registered_lines(tx) -> tuple[dict, ...]:
    """데이터 관리에서 등록한 라인 [{"site", "line", "fab"}] (관리 설정 ``registered_lines.v1``, 11-11 6차).

    fab은 'MEMORY' · 'FOUNDRY' 두 가지다(6차: 메모리/파운드리 구분, Fab 추가 없음).
    """
    entries = _setting(tx, LINES_KEY).get("lines")
    found = []
    for entry in entries if isinstance(entries, list) else ():
        if (isinstance(entry, dict) and isinstance(entry.get("site"), str) and isinstance(entry.get("line"), str)
                and entry.get("fab") in FABS):
            found.append({"site": entry["site"], "line": entry["line"], "fab": entry["fab"]})
    return tuple(found)


def independent_groups(tx) -> frozenset:
    """'독립 설비군으로 보기'로 정한 개조형 설비군 ID (관리 설정 ``independent_groups.v1``, 11-11 VOC 2: DFB).

    DB의 상위 설비군 연결은 그대로 둔다(릴리즈 노트 · SW 계열은 상위 설비군 것을 쓴다). 대시보드 · 히트맵 · 보고서 ·
    내보내기의 설비군 묶음에서 이 ID를 상위 설비군과 따로 보이게 하는 것은 각 화면 담당이 이 함수를 읽어 반영한다.
    """
    ids = _setting(tx, INDEPENDENT_KEY).get("group_ids")
    return frozenset(i for i in ids if isinstance(i, int)) if isinstance(ids, list) else frozenset()


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


def _natural_key(text):
    """'P10'이 'P9' 뒤에 오도록 숫자는 숫자로 비교한다."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part.casefold()) for part in re.split(r"(\d+)", text or "") if part]


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
    independent: bool = False  # 개조형이지만 독립 설비군으로 보기 (DFB 등)


@dataclass(frozen=True)
class SheetRow:
    """DB 값 기준 현황표 한 줄: 설비 현황 하나의 SW 하나 (SW가 없으면 설비 현황 한 줄)."""

    snapshot_id: int
    equipment_id: int
    version_id: int | None
    equipment_code: str
    line: str
    group_id: int | None
    group_name: str
    model_name: str
    production_area: str | None
    version_raw: str
    family_name: str  # 화면에는 보이지 않는다 (11-11 VOC 1)
    pm_names: str
    has_pm: bool
    sources: dict  # 항목 → 원본 셀 주소
    source_values: dict  # 항목 → 원본 셀 값


@dataclass(frozen=True)
class SheetRows:
    batch_id: int
    sheet: str
    workbook: str | None
    site: str
    base_date: date | None
    rows: tuple
    groups: tuple  # (설비군 ID, 이름, 분류) — POLY · OXIDE · KCTC
    lines: tuple  # 이 Site의 라인 (관측 + 등록)


@dataclass(frozen=True)
class LineRow:
    """위치 탭의 라인 한 줄: 관측된 라인과 데이터 관리에서 등록한 라인."""

    site: str
    line: str
    fab: str | None  # MEMORY · FOUNDRY · None(모름)
    equipment_count: int
    registered: bool


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
        """설비 한 행을 더한다. 값: equipment_code · group_name(없으면 model_name으로 설비군을 찾음) · version_raw 필수,
        line · model_name · production_area 선택. ``row_number``가 None이면 원본 칸 위치를 남기지 않는다(DB 값 기준 현황표)."""
        values = {k: (v or "").strip() if isinstance(v, str) or v is None else v for k, v in values.items()}
        code, version, line = (values.get(k, "") for k in ("equipment_code", "version_raw", "line"))
        group_name = values.get("group_name") or values.get("model_name", "")
        require(bool(code) and bool(group_name) and bool(version), "호기 · 설비군 · SW 버전을 모두 적어 주세요.")
        sheet, site, base_date, raw = self._sheet_place(tx, batch_id)
        columns = {} if row_number is None else {
            name: letter for letter, name in self._columns(raw, self._bound_fields(tx, batch_id, sheet)).items()}
        group = next((g for g in self.rows.rows(tx, "equipment_group")
                      if _text(g["group_name"]).casefold() == group_name.casefold()), None)
        require(group is not None, f"설비군 '{group_name}'이(가) 없습니다. 먼저 등록해 주세요.")
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
        from setron.features.reference_data import default_area

        area = values.get("production_area") if values.get("production_area") in FABS else (
            next(iter(areas)) if len(areas) == 1 else default_area(site, line))
        snapshot = tx.insert("equipment_snapshot", {
            "equipment_id": equipment["equipment_id"], "base_date": base_date, "site": site, "line": line or None,
            "model_name": values.get("model_name") or _text(group["group_name"]), "group_id": group["group_id"],
            "production_area": area, "batch_id": batch_id,
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

    def _regroup_in(self, tx, batch_id, snapshot_id, group_id):
        """설비 현황의 설비군을 바꾼다. 모델명이 옛 설비군 이름이었으면 새 이름으로, SW 계열이 새 설비군(또는 상위
        설비군)의 것이 아니면 새 설비군의 계열로 바꾸고 같은 원문 릴리즈에 다시 잇는다. 옛 설비군 ID를 돌려준다."""
        snapshot = tx.get("equipment_snapshot", {"snapshot_id": snapshot_id})
        require(not self.rows.rows(tx, "equipment_pm", snapshot_id=snapshot_id),
                "PM이 있는 설비는 PM마다 설비군이 달라 여기서 바꿀 수 없습니다.")
        groups = {g["group_id"]: g for g in self.rows.rows(tx, "equipment_group")}
        require(group_id in groups, "없는 설비군입니다. 다시 읽어 주세요.")
        old_id, new = snapshot.get("group_id"), groups[group_id]
        if old_id == group_id:
            return old_id
        old_name = _text(groups.get(old_id, {}).get("group_name"))
        changes = {"group_id": group_id}
        if _text(snapshot.get("model_name")) in ("", old_name):
            changes["model_name"] = _text(new["group_name"])
        owners = {group_id} | ({new["parent_group_id"]} if new.get("parent_group_id") else set())
        owned = {f["family_id"] for owner in owners for f in self.rows.rows(tx, "version_family", group_id=owner)}
        # 계열은 설비군을 바꾸기 전에 정한다 (먼저 바꾸면 이 설비가 새 설비군의 '같은 설비'로 잡혀 옛 계열을 다시 고른다)
        for version in self.rows.rows(tx, "equipment_version", snapshot_id=snapshot_id):
            if version["family_id"] in owned:
                continue
            family = self._family_for(tx, batch_id, new, _text(snapshot.get("line")))
            release = next((r["release_version_id"] for r in self.rows.rows(tx, "release_version", family_id=family)
                            if _text(r["version_raw"]) == _text(version["version_raw"])), None)
            tx.update("equipment_version", {"equipment_version_id": version["equipment_version_id"]},
                      {"family_id": family, "release_version_id": release})
        tx.update("equipment_snapshot", {"snapshot_id": snapshot_id}, changes)
        return old_id

    def apply_rows(self, batch_id, adds=(), deletes=(), regroup=()) -> dict:
        """현황표 [저장]의 행 추가 · 삭제 · 설비군 변경을 한 트랜잭션으로 반영한다 (11-11 4차 · 6차).

        param
        - adds: [(값 dict, 새 행 번호 또는 None)] — :meth:`add_row`와 같은 값.
        - deletes: 지울 설비 현황 ID 목록.
        - regroup: [(설비 현황 ID, 새 설비군 ID)].

        return
        - {"added": [새 설비 현황 ID], "journal": 삭제 기록 ID 또는 None, "regroup": [(설비 현황 ID, 옛 설비군 ID)]}
        """
        with self.storage.transaction() as tx:
            back = [(sid, self._regroup_in(tx, batch_id, sid, gid)) for sid, gid in regroup if sid not in set(deletes)]
            journal = self._delete_rows_in(tx, batch_id, deletes) if deletes else None
            added = [self._add_row_in(tx, batch_id, values, row) for values, row in adds]
            if back:
                names = {g["group_id"]: _text(g["group_name"]) for g in self.rows.rows(tx, "equipment_group")}
                tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                    "kind": "regroup", "table": "equipment_snapshot", "label": f"설비 {len(back)}대",
                    "before": ", ".join(sorted({names.get(o, "?") for _, o in back})),
                    "after": ", ".join(sorted({names.get(g, "?") for _, g in regroup}))}}})
        return {"added": added, "journal": journal, "regroup": back}

    def undo_rows(self, batch_id, added=(), journal=None, regroup=()) -> None:
        """[저장]으로 반영한 행 추가 · 삭제 · 설비군 변경을 한 트랜잭션으로 되돌린다."""
        with self.storage.transaction() as tx:
            if added:
                self._delete_rows_in(tx, batch_id, added, undo_add=True)
            if journal is not None:
                self._restore_rows_in(tx, journal)
            for sid, old in regroup:
                if old is not None and self.rows.rows(tx, "equipment_snapshot", snapshot_id=sid):
                    self._regroup_in(tx, batch_id, sid, old)

    def sheet_rows(self, batch_id) -> SheetRows:
        """현황표를 DB 값 기준으로 읽는다 (11-11 6차): 설비 × SW 한 줄, 가져오기 3단계와 같은 항목.

        SW 계열은 화면에 보이지 않는다(11-11 VOC 1). 칸마다 원본 셀 위치 · 값을 함께 돌려 [원본 보기] · 툴팁에 쓴다.
        """
        with self.storage.transaction() as tx:
            sheet, site, base_date, raw = self._sheet_place_or_empty(tx, batch_id)
            cells = dict(raw.get(sheet) or {})
            equipment = {e["equipment_id"]: _text(e["equipment_code"]) for e in self.rows.rows(tx, "equipment")}
            groups = self.rows.rows(tx, "equipment_group")
            names = {g["group_id"]: _text(g["group_name"]) for g in groups}
            families = {f["family_id"]: _text(f["family_name"]) for f in self.rows.rows(tx, "version_family")}
            rows = []
            for snapshot in self.rows.rows(tx, "equipment_snapshot", batch_id=batch_id):
                versions = self.rows.rows(tx, "equipment_version", snapshot_id=snapshot["snapshot_id"])
                pms = self.rows.rows(tx, "equipment_pm", snapshot_id=snapshot["snapshot_id"])
                pm_names = defaultdict(list)
                for pm in pms:
                    pm_names[pm.get("equipment_version_id")].append(_text(pm["pm_name"]))

                def source(row, name):
                    for location in _locations(row, name):
                        if location.get("sheet") == sheet and location.get("batch_id", row.get("batch_id")) == batch_id:
                            address = (location.get("cells") or [None])[0]
                            if address:
                                return address
                    return None

                # 설비군은 원본의 모델 칸에서 정해진다 (가져올 때 남긴 group_name 출처는 제목 칸을 가리키는 경우가 있어 쓰지 않음)
                model_cell = source(snapshot, "model_name")
                base = {"equipment_code": source(snapshot, "equipment_code"), "line": source(snapshot, "line"),
                        "model_name": model_cell, "group_name": model_cell}
                for version in versions or [None]:
                    sources = {**base, "version_raw": source(version, "version_raw") if version else None}
                    rows.append(SheetRow(
                        snapshot["snapshot_id"], snapshot["equipment_id"], version["equipment_version_id"] if version else None,
                        equipment.get(snapshot["equipment_id"], ""), _text(snapshot.get("line")), snapshot.get("group_id"),
                        names.get(snapshot.get("group_id"), ""), _text(snapshot.get("model_name")), snapshot.get("production_area"),
                        _text(version["version_raw"]) if version else "", families.get(version["family_id"], "") if version else "",
                        ", ".join(pm_names.get(version["equipment_version_id"], [])) if version else "", bool(pms),
                        {k: v for k, v in sources.items() if v}, {k: _text(cells.get(v)) for k, v in sources.items() if v}))
            lines = sorted({r["line"] for r in registered_lines(tx) if r["site"] == site}
                           | {_text(s.get("line")) for s in self.rows.rows(tx, "equipment_snapshot") if _text(s.get("site")) == site
                              and s.get("line")}, key=_natural_key)
            choices = tuple((g["group_id"], names[g["group_id"]], _text(g["category"])) for g in sorted(
                groups, key=lambda g: names[g["group_id"]]) if _text(g["category"]) in CATEGORIES)
        return SheetRows(batch_id, sheet, _workbook(raw), site, base_date, tuple(rows), choices, tuple(lines))

    def _sheet_place_or_empty(self, tx, batch_id):
        raw = _raw(tx.get("import_batch", {"batch_id": batch_id}).get("raw_data"))
        try:
            return self._sheet_place(tx, batch_id)
        except Exception:  # noqa: BLE001 — 시트 · Site를 모르는 옛 입력 작업도 표는 보인다
            names = sheet_names(raw)
            return (names[0] if names else ""), "", None, raw

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
    def _release_service(self):
        from setron.features.release.service import ReleaseService

        return ReleaseService(self.storage)

    def _add_version_in(self, tx, family_id, version_raw, released_at=None, *, journal=True):
        from setron.features.release.schemas import ReleaseInput

        version_raw = (version_raw or "").strip()
        require(bool(version_raw), "버전명을 적어 주세요.")
        current = self.rows.rows(tx, "release_version", family_id=family_id)
        require(all(_text(r["version_raw"]) != version_raw for r in current), f"'{version_raw}'은(는) 이미 있는 버전입니다.")
        orders = [r["version_order"] for r in current if r.get("version_order") is not None]
        detail = self._release_service().register_release_in(tx, ReleaseInput(
            family_id=family_id, version_raw=version_raw, released_at=released_at,
            version_order=max(orders) + 10 if orders else None))
        release_id = detail.release.current["release_version_id"]
        self._relink(tx, family_id, version_raw, release_id)
        if journal:
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_create": {
                "table": "release_version", "values": {"label": version_raw, "release_version_id": release_id}}}})
        return release_id

    def _relink(self, tx, family_id, version_raw, release_id):
        """같은 계열 · 같은 원문인데 릴리즈 연결이 없는 설비 SW를 이 버전에 잇는다."""
        for snapshot_version in self.rows.rows(tx, "equipment_version", family_id=family_id):
            if snapshot_version.get("release_version_id") is None and _text(snapshot_version["version_raw"]) == version_raw:
                tx.update("equipment_version", {"equipment_version_id": snapshot_version["equipment_version_id"]},
                          {"release_version_id": release_id})

    def _add_item_in(self, tx, release_version_id, content, group_ids=None, item_no=None):
        from setron.features.item.schemas import ItemState, TargetScope
        from setron.features.release.schemas import ItemInput, ItemLinkInput
        from setron.features.release.validation import prepare_record

        content = (content or "").strip()
        require(bool(content), "Item 내용을 적어 주세요.")
        if item_no is None:  # 번호는 적지 않고 그 버전의 맨 뒤 번호 다음 (순서는 끌어서 바꾼다)
            numbers = [tx.get("item", {"item_id": link["item_id"]}).get("item_no") for link in
                       self.rows.rows(tx, "release_version_item", release_version_id=release_version_id)]
            item_no = max((n for n in numbers if isinstance(n, int)), default=0) + 1
        service = self._release_service()
        release = service.repository.get(tx, "release_version", {"release_version_id": release_version_id}, lock=True)
        item = service._insert(tx, "item", prepare_record(ItemInput(content=content, item_no=item_no)), {})
        ids = tuple(group_ids or ())
        service._link(tx, release, ItemLinkInput(item_id=item["item_id"], item_state=ItemState.APPLIED,
                                                 target_scope=TargetScope.LISTED if ids else TargetScope.ALL,
                                                 target_group_ids=ids))
        tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_create": {
            "table": "item", "values": {"label": content[:40], "item_id": item["item_id"],
                                        "release_version_id": release_version_id}}}})
        return item["item_id"]

    def add_release_version(self, family_id, version_raw, released_at=None) -> int:
        """계열에 새 버전을 더한다. 순서는 맨 끝(기존 최대 + 10)이고, 같은 원문의 설비 SW 중 연결이 없는 것은 이 버전에 잇는다."""
        with self.storage.transaction() as tx:
            return self._add_version_in(tx, family_id, version_raw, released_at)

    def add_release_item(self, release_version_id, content, item_no=None, group_ids=None) -> int:
        """버전에 새 Item을 더한다. 적용 모델을 적지 않으면 '전체 설비'로 둔다 (11-11 팀 결정과 같은 기본값)."""
        with self.storage.transaction() as tx:
            return self._add_item_in(tx, release_version_id, content, group_ids, item_no)

    def _delete_release_in(self, tx, release_version_id, item_id=None, *, undo=False):
        """릴리즈 줄 하나를 지우고 되살릴 수 있게 지운 버전 · Item · 연결 전체를 기록에 남긴다. 기록 ID를 돌려준다."""
        release = tx.get("release_version", {"release_version_id": release_version_id})
        links = self.rows.rows(tx, "release_version_item", release_version_id=release_version_id)
        targets = [link for link in links if item_id is None or link["item_id"] == item_id]
        require(bool(targets) or item_id is None, "이미 지운 Item입니다. 다시 읽어 주세요.")
        removed = []
        for link in targets:
            item = tx.get("item", {"item_id": link["item_id"]})
            tx.repository.delete(tx.session, "release_version_item",
                                 {"release_version_id": release_version_id, "item_id": link["item_id"]})
            shared = bool(self.rows.rows(tx, "release_version_item", item_id=link["item_id"]))
            removed.append({"item_id": link["item_id"], "item_no": item.get("item_no"), "content": _text(item.get("content")),
                            "item": _plain(item), "link": _plain(link), "kept": shared})
            if not shared:
                tx.delete_release_item(link["item_id"])
        if item_id is None:
            tx.delete_release_version(release_version_id)
        label = _text(release["version_raw"]) + (f" · Item {removed[0]['content'][:30]}" if item_id is not None else "")
        journal = tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_delete_release": {
            "release_version_id": release_version_id, "family_id": release["family_id"], "version_raw": _text(release["version_raw"]),
            "item_id": item_id, "items": removed, "label": label, "release": _plain(release) if item_id is None else None,
            **({"undo": True} if undo else {})}}})
        return journal["batch_id"]

    def delete_release_rows(self, release_version_id, item_id=None) -> str:
        """릴리즈노트 줄 삭제. ``item_id``가 있으면 그 버전에서 Item 하나를, 없으면 버전과 그 Item 연결을 모두 지운다.

        다른 버전에 연결되지 않은 Item은 함께 지운다. 위험 지정이 가리키던 버전 · Item은 근거를 남기고 '연결 미확인'으로
        바꾸며(StorageTransaction.delete_release_*), 설비 SW의 릴리즈 연결은 DB 규칙대로 비워진다.

        return
        - 지운 줄 이름 (안내용).
        """
        with self.storage.transaction() as tx:
            journal = self._delete_release_in(tx, release_version_id, item_id)
            return _raw(tx.get("import_batch", {"batch_id": journal})["raw_data"])["management_delete_release"]["label"]

    def _restore_release_in(self, tx, journal_id):
        """릴리즈 줄 삭제 기록 하나를 되살린다. 버전 · Item은 새 ID로 다시 넣고 같은 연결 상태 · 적용 모델을 잇는다.

        위험 지정의 연결은 되살리지 않는다(삭제 때 '연결 미확인'으로 바뀐 그대로).
        """
        removed = _raw(tx.get("import_batch", {"batch_id": journal_id}).get("raw_data")).get("management_delete_release")
        require(isinstance(removed, dict), "되살릴 릴리즈 삭제 기록이 아닙니다.")
        require(all(value.get("batch_id") != journal_id for _, value in self._journals(tx, "management_restore_release")),
                "이미 되살린 삭제입니다.")
        release_id = removed["release_version_id"]
        if removed.get("release"):
            values = {k: v for k, v in removed["release"].items() if k != "release_version_id"}
            if values.get("released_at"):
                values["released_at"] = date.fromisoformat(values["released_at"][:10])
            require(all(_text(r["version_raw"]) != values["version_raw"]
                        for r in self.rows.rows(tx, "release_version", family_id=values["family_id"])),
                    f"'{values['version_raw']}' 버전이 다시 생겨 되살릴 수 없습니다.")
            release_id = tx.insert("release_version", values)["release_version_id"]
            self._relink(tx, values["family_id"], values["version_raw"], release_id)
        for entry in removed.get("items") or ():
            item_id = entry["item_id"]
            if not entry.get("kept"):
                values = {k: v for k, v in entry["item"].items() if k != "item_id"}
                item_id = tx.insert("item", values)["item_id"]
            link = {k: v for k, v in entry["link"].items() if k not in ("release_version_id", "item_id")}
            tx.insert("release_version_item", {"release_version_id": release_id, "item_id": item_id, **link})
        tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_restore_release": {
            "batch_id": journal_id, "label": removed.get("label"), "release_version_id": release_id}}})
        return release_id

    def _item_order(self, tx, release_version_id):
        links = self.rows.rows(tx, "release_version_item", release_version_id=release_version_id)
        numbered = [(tx.get("item", {"item_id": link["item_id"]}).get("item_no"), link["item_id"]) for link in links]
        return [item_id for _, item_id in sorted(numbered, key=lambda pair: (pair[0] is None, pair[0] or 0, pair[1]))]

    def _reorder_in(self, tx, release_version_id, item_ids, *, journal=True):
        ids = [int(i) for i in item_ids]
        links = {link["item_id"] for link in self.rows.rows(tx, "release_version_item", release_version_id=release_version_id)}
        require(set(ids) == links and len(ids) == len(links), "Item 목록이 바뀌었습니다. 다시 읽어 주세요.")
        before = []
        for number, item_id in enumerate(ids, 1):
            item = tx.get("item", {"item_id": item_id})
            before.append((item.get("item_no"), _text(item.get("content"))[:20]))
            if item.get("item_no") != number:
                tx.update("item", {"item_id": item_id}, {"item_no": number})
        if journal:
            release = tx.get("release_version", {"release_version_id": release_version_id})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_reorder": {
                "release_version_id": release_version_id, "version_raw": _text(release["version_raw"]),
                "before": [f"{n}. {c}" for n, c in sorted(before, key=lambda pair: (pair[0] is None, pair[0] or 0))],
                "after": [f"{i}. {c}" for i, (_, c) in enumerate(before, 1)]}}})

    def reorder_items(self, release_version_id, item_ids) -> None:
        """버전 안 Item 순서를 바꾼다 (끌어서 놓기). 주어진 순서대로 Item 번호를 1부터 다시 매긴다."""
        with self.storage.transaction() as tx:
            self._reorder_in(tx, release_version_id, item_ids)

    def apply_release(self, family_id, plan) -> dict:
        """릴리즈노트 [저장]의 버전 · Item 추가, Item 순서, 삭제를 한 트랜잭션으로 반영한다 (11-11 5차).

        param
        - plan: {"add_versions": [{"temp", "version_raw", "released_at"}], "add_items": [{"temp", "release", "content",
          "group_ids"}] ("release"는 릴리즈 ID나 새 버전의 temp), "orders": {릴리즈 ID 또는 temp: [Item ID 또는 새 Item temp]},
          "delete_items": [(릴리즈 ID, Item ID)], "delete_versions": [릴리즈 ID]}

        return
        - 되돌리기 정보 {"versions": [...], "items": [(릴리즈 ID, Item ID)], "journals": [...], "orders": {릴리즈 ID: 이전 순서}}
        """
        undo = {"versions": [], "items": [], "journals": [], "orders": {}}
        with self.storage.transaction() as tx:
            temps = {}
            for version in plan.get("add_versions") or ():
                released = version.get("released_at")
                temps[version["temp"]] = self._add_version_in(
                    tx, family_id, version["version_raw"], date.fromisoformat(released) if isinstance(released, str) and released
                    else released or None)
                undo["versions"].append(temps[version["temp"]])
            for item in plan.get("add_items") or ():
                release = temps.get(item["release"], item["release"])
                temps[item["temp"]] = self._add_item_in(tx, release, item["content"], item.get("group_ids"))
                if release not in undo["versions"]:
                    undo["items"].append((release, temps[item["temp"]]))
            for key, order in (plan.get("orders") or {}).items():
                release = temps.get(key, key)
                if release not in undo["versions"]:
                    undo["orders"][release] = self._item_order(tx, release)
                self._reorder_in(tx, release, [temps.get(i, i) for i in order])
            for release, item_id in plan.get("delete_items") or ():
                undo["journals"].append(self._delete_release_in(tx, release, item_id))
            for release in plan.get("delete_versions") or ():
                undo["journals"].append(self._delete_release_in(tx, release))
        return undo

    def undo_release(self, undo) -> None:
        """:meth:`apply_release`로 반영한 추가 · 순서 · 삭제를 한 트랜잭션으로 되돌린다 (추가한 것은 지우고 지운 것은 되살린다)."""
        with self.storage.transaction() as tx:
            for release, item_id in undo.get("items") or ():
                self._delete_release_in(tx, release, item_id, undo=True)
            for release in undo.get("versions") or ():
                self._delete_release_in(tx, release, undo=True)
            for journal in reversed(undo.get("journals") or ()):
                self._restore_release_in(tx, journal)
            for release, order in (undo.get("orders") or {}).items():
                current = set(self._item_order(tx, release))
                if current and set(order) == current:  # 지웠다 되살린 Item(새 ID)이 섞였으면 번호 순서를 그대로 둔다
                    self._reorder_in(tx, release, order, journal=False)

    # --- 설비·설비군 -------------------------------------------------------------------
    def equipment_overview(self) -> tuple[tuple[GroupRow, ...], tuple[EquipmentRow, ...]]:
        with self.storage.transaction() as tx:
            groups = self.rows.rows(tx, "equipment_group")
            equipment = self.rows.rows(tx, "equipment")
            snapshots = self.rows.rows(tx, "equipment_snapshot")
            pms = self.rows.rows(tx, "equipment_pm")
            independent = independent_groups(tx)
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
                                    names.get(g.get("parent_group_id")), per_group[g["group_id"]],
                                    g["group_id"] in independent) for g in groups)
        return group_rows, tuple(sorted(rows, key=lambda r: r.code))

    def create_group(self, name, category, parent_id=None) -> GroupRow:
        """새 설비군(개조형이면 상위 설비군 지정)을 등록한다. 같은 이름은 거부한다.

        기본형이면 같은 이름의 SW 계열을 함께 만든다(11-11 6차: 가져오기에 SW 계열 입력이 필요 없게, 새 설비군의 릴리즈
        노트를 바로 넣을 수 있게). 개조형은 상위 설비군의 계열을 쓴다.
        """
        name = (name or "").strip()
        require(bool(name) and len(name) <= 50, "설비군 이름을 50자 이내로 입력해 주세요.")
        require(category in CATEGORIES, "분류는 POLY · OXIDE · KCTC 중 하나여야 합니다.")
        with self.storage.transaction() as tx:
            groups = self.rows.rows(tx, "equipment_group")
            same = next((g for g in groups if _text(g["group_name"]).casefold() == name.casefold()), None)
            if same is not None and same.get("parent_group_id"):
                parent_name = next((_text(g["group_name"]) for g in groups if g["group_id"] == same["parent_group_id"]), "상위 설비군")
                require(False, f"'{_text(same['group_name'])}'은(는) 이미 '{parent_name}'의 개조형으로 등록돼 있습니다. "
                               "설비군 목록에서 고른 뒤 [독립 설비군으로 보기]를 눌러 주세요.")
            require(same is None, "이미 있는 설비군 이름입니다.")
            parent = None
            if parent_id is not None:
                parent = next((g for g in groups if g["group_id"] == parent_id), None)
                require(parent is not None, "상위 설비군을 찾지 못했습니다.")
                require(_text(parent["category"]) == category, "개조형은 상위 설비군과 같은 분류여야 합니다.")
            created = tx.insert("equipment_group", {"group_name": name, "category": category,
                                                    "parent_group_id": parent_id, "analysis_enabled": True})
            family = None
            if parent_id is None:
                family = tx.insert("version_family", {"group_id": created["group_id"], "family_name": name[:50],
                                                      "ui_type": None})["family_id"]
            journal = {"schema": 1, "management_create": {"table": "equipment_group", "values": {
                "group_id": created["group_id"], "group_name": name, "category": category, "parent_group_id": parent_id,
                "family_id": family}}}
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": journal})
        return GroupRow(created["group_id"], name, category, parent_id,
                        _text(parent["group_name"]) if parent else None, 0)

    def _group_usage(self, tx, group_id):
        """설비군이 쓰이는 곳 (안내 문장 목록). 비어 있으면 이름을 바꾸거나 지워도 된다."""
        used = []
        if any(s.get("group_id") == group_id for s in self.rows.rows(tx, "equipment_snapshot")) or \
                any(p.get("group_id") == group_id for p in self.rows.rows(tx, "equipment_pm")):
            used.append("설비 현황")
        if any(g.get("parent_group_id") == group_id for g in self.rows.rows(tx, "equipment_group")):
            used.append("개조형 설비군")
        families = [f["family_id"] for f in self.rows.rows(tx, "version_family", group_id=group_id)]
        if any(r["family_id"] in families for r in self.rows.rows(tx, "release_version")):
            used.append("릴리즈 노트")
        if any(group_id in (link.get("target_group_ids") or ()) for link in self.rows.rows(tx, "release_version_item")):
            used.append("Item 적용 모델")
        if any(r.get("family_id") in families for r in self.rows.rows(tx, "risk_rule")):
            used.append("위험 지정")
        return used, families

    def rename_group(self, group_id, name) -> str:
        """설비군 이름을 바꾼다. 설비 현황 · 릴리즈 노트 등에 쓰이는 설비군은 이름으로 연결되는 곳이 있어 막는다."""
        name = (name or "").strip()
        require(bool(name) and len(name) <= 50, "설비군 이름을 50자 이내로 입력해 주세요.")
        with self.storage.transaction() as tx:
            group = tx.get("equipment_group", {"group_id": group_id})
            old = _text(group["group_name"])
            require(all(_text(g["group_name"]).casefold() != name.casefold() for g in self.rows.rows(tx, "equipment_group")
                        if g["group_id"] != group_id), "이미 있는 설비군 이름입니다.")
            used, families = self._group_usage(tx, group_id)
            require(not used, f"'{old}'은(는) {', '.join(used)}에 쓰이고 있어 이름을 바꿀 수 없습니다. "
                              "새 이름으로 설비군을 추가해 주세요.")
            tx.update("equipment_group", {"group_id": group_id}, {"group_name": name})
            for family in self.rows.rows(tx, "version_family", group_id=group_id):
                if _text(family["family_name"]) == old:  # 함께 만든 기본 계열 이름도 맞춘다
                    tx.update("version_family", {"family_id": family["family_id"]}, {"family_name": name[:50]})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "rename", "table": "equipment_group", "label": name, "before": old, "after": name}}})
        return name

    def delete_group(self, group_id) -> str:
        """쓰이지 않는 설비군(과 함께 만든 빈 SW 계열)을 지운다."""
        with self.storage.transaction() as tx:
            group = tx.get("equipment_group", {"group_id": group_id})
            name = _text(group["group_name"])
            used, families = self._group_usage(tx, group_id)
            require(not used, f"'{name}'은(는) {', '.join(used)}에 쓰이고 있어 지울 수 없습니다.")
            for family_id in families:
                tx.repository.delete(tx.session, "version_family", {"family_id": family_id})
            tx.repository.delete(tx.session, "equipment_group", {"group_id": group_id})
            ids = set(independent_groups(tx)) - {group_id}
            _save_setting(tx, INDEPENDENT_KEY, {"group_ids": sorted(ids)})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "delete", "table": "equipment_group", "label": name, "before": name, "after": "삭제"}}})
        return name

    def set_independent(self, group_id, independent) -> bool:
        """개조형 설비군을 화면에서 독립 설비군으로 볼지 정한다 (11-11 VOC 2: DFB). DB의 상위 설비군 연결은 그대로 둔다."""
        with self.storage.transaction() as tx:
            group = tx.get("equipment_group", {"group_id": group_id})
            require(group.get("parent_group_id") is not None, "기본형 설비군은 이미 독립 설비군입니다.")
            ids = set(independent_groups(tx))
            ids = ids | {group_id} if independent else ids - {group_id}
            _save_setting(tx, INDEPENDENT_KEY, {"group_ids": sorted(ids)})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "independent", "table": "equipment_group", "label": _text(group["group_name"]),
                "before": "독립" if not independent else "상위 설비군에 묶음",
                "after": "독립" if independent else "상위 설비군에 묶음"}}})
        return bool(independent)

    # --- 위치(Site · 라인) ------------------------------------------------------------------
    def sites(self) -> tuple[str, ...]:
        """데이터 관리에서 등록한 Site (관측이 없어도 조회 선택지에 보인다)."""
        with self.storage.transaction() as tx:
            return registered_sites(tx)

    def _known_sites(self, tx):
        return {s.casefold(): s for s in registered_sites(tx)} | {
            _text(s["site"]).casefold(): _text(s["site"]) for s in self.rows.rows(tx, "equipment_snapshot") if s.get("site")}

    def add_site(self, name) -> str:
        """Site를 관리 설정에 등록한다. 관측된 Site나 이미 등록한 이름은 거부한다."""
        name = (name or "").strip()
        require(bool(name) and len(name) <= 50, "Site 이름을 50자 이내로 입력해 주세요.")
        with self.storage.transaction() as tx:
            require(name.casefold() not in self._known_sites(tx), "이미 있는 Site입니다.")
            _save_setting(tx, SITES_KEY, {"sites": [*registered_sites(tx), name]})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {
                "schema": 1, "management_create": {"table": "site", "values": {"site": name}}}})
        return name

    def rename_site(self, old, new) -> int:
        """Site 이름을 바꾼다. 그 Site의 모든 설비 현황 · 등록 라인 · 등록 Site 이름을 함께 바꾼다.

        return
        - 이름을 바꾼 설비 현황 수.
        """
        new = (new or "").strip()
        require(bool(new) and len(new) <= 50, "Site 이름을 50자 이내로 입력해 주세요.")
        with self.storage.transaction() as tx:
            known = self._known_sites(tx)
            require(old.casefold() in known, "없는 Site입니다. 다시 읽어 주세요.")
            require(new.casefold() == old.casefold() or new.casefold() not in known, "이미 있는 Site입니다.")
            count = 0
            for snapshot in self.rows.rows(tx, "equipment_snapshot"):
                if _text(snapshot.get("site")) == old:
                    tx.update("equipment_snapshot", {"snapshot_id": snapshot["snapshot_id"]}, {"site": new})
                    count += 1
            sites = [new if s == old else s for s in registered_sites(tx)]
            _save_setting(tx, SITES_KEY, {"sites": sites})
            lines = [{**entry, "site": new} if entry["site"] == old else entry for entry in registered_lines(tx)]
            _save_setting(tx, LINES_KEY, {"lines": lines})
            for batch in self.rows.rows(tx, "import_batch", source_type="MANUAL"):  # 직접 만든 빈 현황표의 Site
                raw = _raw(batch.get("raw_data"))
                meta = raw.get(SHEET_KEY)
                if isinstance(meta, dict) and meta.get("site") == old:
                    tx.update("import_batch", {"batch_id": batch["batch_id"]}, {"raw_data": {**raw, SHEET_KEY: {**meta, "site": new}}})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "rename", "table": "site", "label": new, "before": old, "after": new, "count": count}}})
        self._apply_lines()
        return count

    def delete_site(self, name) -> str:
        """설비 현황이 없는 등록 Site를 지운다 (그 Site에 등록한 라인도 함께)."""
        with self.storage.transaction() as tx:
            require(all(_text(s.get("site")) != name for s in self.rows.rows(tx, "equipment_snapshot")),
                    f"'{name}'에는 설비 현황이 있어 지울 수 없습니다. 가져온 엑셀 삭제로 현황을 먼저 지워 주세요.")
            sites = registered_sites(tx)
            require(name in sites, "데이터 관리에서 등록한 Site만 지울 수 있습니다.")
            _save_setting(tx, SITES_KEY, {"sites": [s for s in sites if s != name]})
            _save_setting(tx, LINES_KEY, {"lines": [e for e in registered_lines(tx) if e["site"] != name]})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "delete", "table": "site", "label": name, "before": name, "after": "삭제"}}})
        self._apply_lines()
        return name

    def lines(self) -> tuple[LineRow, ...]:
        """관측된 라인(설비별 최근 현황)과 등록한 라인을 합친 목록. Fab은 등록값 → 현황의 생산 영역 → 장소표 순서."""
        from setron.features.reference_data import default_area

        with self.storage.transaction() as tx:
            snapshots = self.rows.rows(tx, "equipment_snapshot")
            registered = registered_lines(tx)
        latest = {}
        for s in snapshots:
            if s["equipment_id"] not in latest or s["base_date"] > latest[s["equipment_id"]]["base_date"]:
                latest[s["equipment_id"]] = s
        counts, areas = Counter(), defaultdict(Counter)
        for s in latest.values():
            key = (_text(s.get("site")), _text(s.get("line")))
            if key[0] and key[1]:
                counts[key] += 1
        for s in snapshots:
            key = (_text(s.get("site")), _text(s.get("line")))
            if key[0] and key[1] and s.get("production_area"):
                areas[key][s["production_area"]] += 1
        fabs = {(e["site"], e["line"]): e["fab"] for e in registered}
        keys = set(counts) | set(fabs) | set(areas)
        rows = []
        for site, line in keys:
            fab = fabs.get((site, line)) or (areas[(site, line)].most_common(1)[0][0] if areas[(site, line)] else None) \
                or default_area(site, line)
            rows.append(LineRow(site, line, fab, counts[(site, line)], (site, line) in fabs))
        return tuple(sorted(rows, key=lambda r: (r.site, r.fab or "", _natural_key(r.line))))

    def add_line(self, site, line, fab) -> str:
        """Site의 메모리/파운드리 아래에 라인을 등록한다. 같은 Site에 같은 라인(대소문자 무시)이 있으면 거부한다."""
        line = (line or "").strip()
        require(bool(line) and len(line) <= 50, "라인 이름을 50자 이내로 입력해 주세요.")
        require(fab in FABS, "메모리 또는 파운드리를 골라 주세요.")
        with self.storage.transaction() as tx:
            require(site.casefold() in self._known_sites(tx), "없는 Site입니다. Site를 먼저 추가해 주세요.")
            existing = {(e["site"], e["line"].casefold()) for e in registered_lines(tx)} | {
                (_text(s.get("site")), _text(s.get("line")).casefold()) for s in self.rows.rows(tx, "equipment_snapshot")}
            require((site, line.casefold()) not in existing, f"{site}에 이미 있는 라인입니다.")
            _save_setting(tx, LINES_KEY, {"lines": [*registered_lines(tx), {"site": site, "line": line, "fab": fab}]})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "create", "table": "line", "label": f"{site} · {line}", "before": "", "after": FABS[fab]}}})
        self._apply_lines()
        return line

    def set_line_fab(self, site, line, fab) -> int:
        """라인의 메모리/파운드리를 정한다. 등록값을 바꾸고 그 라인 설비 현황의 생산 영역도 함께 바꾼다.

        설비 SW의 계열(파운드리용 릴리즈 노트)은 바꾸지 않는다. 계열까지 맞추려면 그 현황표를 다시 가져온다.

        return
        - 생산 영역을 바꾼 설비 현황 수.
        """
        require(fab in FABS, "메모리 또는 파운드리를 골라 주세요.")
        with self.storage.transaction() as tx:
            entries = [e for e in registered_lines(tx) if not (e["site"] == site and e["line"] == line)]
            before = next((e["fab"] for e in registered_lines(tx) if e["site"] == site and e["line"] == line), None)
            _save_setting(tx, LINES_KEY, {"lines": [*entries, {"site": site, "line": line, "fab": fab}]})
            count = 0
            for snapshot in self.rows.rows(tx, "equipment_snapshot"):
                if _text(snapshot.get("site")) == site and _text(snapshot.get("line")) == line:
                    before = before or snapshot.get("production_area")
                    if snapshot.get("production_area") != fab:
                        tx.update("equipment_snapshot", {"snapshot_id": snapshot["snapshot_id"]}, {"production_area": fab})
                        count += 1
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "fab", "table": "line", "label": f"{site} · {line}", "before": FABS.get(before, "모름"),
                "after": FABS[fab], "count": count}}})
        self._apply_lines()
        return count

    def rename_line(self, site, old, new) -> int:
        """라인 이름을 바꾼다. 그 Site · 라인의 모든 설비 현황과 등록값을 함께 바꾼다. 바꾼 설비 현황 수를 돌려준다."""
        new = (new or "").strip()
        require(bool(new) and len(new) <= 50, "라인 이름을 50자 이내로 입력해 주세요.")
        with self.storage.transaction() as tx:
            existing = {e["line"].casefold() for e in registered_lines(tx) if e["site"] == site} | {
                _text(s.get("line")).casefold() for s in self.rows.rows(tx, "equipment_snapshot") if _text(s.get("site")) == site}
            require(new.casefold() == old.casefold() or new.casefold() not in existing, f"{site}에 이미 있는 라인입니다.")
            count = 0
            for snapshot in self.rows.rows(tx, "equipment_snapshot"):
                if _text(snapshot.get("site")) == site and _text(snapshot.get("line")) == old:
                    tx.update("equipment_snapshot", {"snapshot_id": snapshot["snapshot_id"]}, {"line": new})
                    count += 1
            lines = [{**e, "line": new} if e["site"] == site and e["line"] == old else e for e in registered_lines(tx)]
            _save_setting(tx, LINES_KEY, {"lines": lines})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "rename", "table": "line", "label": f"{site} · {new}", "before": old, "after": new, "count": count}}})
        self._apply_lines()
        return count

    def delete_line(self, site, line) -> str:
        """설비 현황이 없는 등록 라인을 지운다."""
        with self.storage.transaction() as tx:
            require(all(not (_text(s.get("site")) == site and _text(s.get("line")) == line)
                        for s in self.rows.rows(tx, "equipment_snapshot")),
                    f"{site} · {line}에는 설비 현황이 있어 지울 수 없습니다. 이름을 바꾸거나 현황을 먼저 지워 주세요.")
            entries = registered_lines(tx)
            require(any(e["site"] == site and e["line"] == line for e in entries), "데이터 관리에서 등록한 라인만 지울 수 있습니다.")
            _save_setting(tx, LINES_KEY, {"lines": [e for e in entries if not (e["site"] == site and e["line"] == line)]})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_registry": {
                "kind": "delete", "table": "line", "label": f"{site} · {line}", "before": line, "after": "삭제"}}})
        self._apply_lines()
        return line

    def _apply_lines(self):
        """등록 라인의 메모리/파운드리를 가져오기 · AI 분석의 판정(장소표 앞)에 바로 쓰이게 한다."""
        from setron.features.reference_data import use_registered_lines

        with self.storage.transaction() as tx:
            use_registered_lines(registered_lines(tx))

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
                    + (f" 외 {len(items) - 3}개" if len(items) > 3 else "") or _text(dropped.get("version_raw")),
                    "추가 취소" if dropped.get("undo") else "삭제", None, None, None))
            registry = raw.get("management_registry")
            if isinstance(registry, dict):
                kind = {"create": "등록", "delete": "삭제"}.get(registry.get("kind"), "수정")
                field = {"rename": "이름", "fab": "메모리/파운드리", "independent": "독립 설비군 보기"}.get(
                    registry.get("kind"), TABLE_LABELS.get(registry.get("table"), ""))
                after = _text(registry.get("after")) + (f" (설비 현황 {registry['count']}건)" if registry.get("count") else "")
                result.append(HistoryEntry(batch["batch_id"], at, kind, _text(registry.get("table")), {},
                                           _text(registry.get("label")), field, _text(registry.get("before")), after,
                                           None, None, None))
            revived = raw.get("management_restore_release")
            if isinstance(revived, dict):
                result.append(HistoryEntry(batch["batch_id"], at, "되돌리기", "release_version", {}, _text(revived.get("label")),
                                           "릴리즈 줄", "삭제", "되살림", None, None, None))
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
