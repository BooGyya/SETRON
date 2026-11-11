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
CATEGORIES = ("POLY", "OXIDE", "KCTC")
SITES_KEY = "registered_sites.v1"
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
    "app_setting": "설정", "equipment_group": "설비군", "site": "Site",
}
RULE_LABELS = {"SAFETY_REQUIRED": "필수", "DEFECT_ITEM": "결함", "DIRECT_VERSION": "위험 버전"}


def with_workbook(raw, workbook_name):
    """가져오기 원본에 파일 이름을 더한다. 이름이 없으면 그대로 둔다."""
    name = (workbook_name or "").strip()
    return {**raw, WORKBOOK_KEY: name} if name else dict(raw)


def sheet_names(raw) -> tuple[str, ...]:
    """raw_data의 시트 이름들 (파일 이름 키 제외)."""
    return tuple(str(k) for k in (raw or {}) if k != WORKBOOK_KEY)


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
        return SheetGrid(batch_id, sheet, _workbook(raw), cells, bindings, owners,
                         {a: v for a, v in deleted.items() if a not in bindings})

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

    def _deleted_cells(self, tx, batch_id, sheet):
        """데이터 관리에서 지운 행의 칸 → '삭제됨 · 호기'. 행 삭제 기록(management_delete)에서 읽는다."""
        found = {}
        for batch in self.rows.rows(tx, "import_batch", source_type="MANUAL"):
            removed = _raw(batch.get("raw_data")).get("management_delete")
            if not isinstance(removed, dict) or removed.get("batch_id") != batch_id or removed.get("sheet") != sheet:
                continue
            for row in removed.get("rows") or ():
                for address in row.get("cells") or ():
                    found[address] = f"삭제됨 · {row.get('equipment_code') or '설비'}"
        return found

    def delete_rows(self, batch_id, snapshot_ids) -> int:
        """현황표의 고른 설비 행을 지운다: 그 관측의 PM → SW → 설비 현황 순서로 한 트랜잭션에서 지운다.

        설비 호기(equipment)와 가져오기 원본(import_batch)은 남긴다 (다른 기준일 관측·감사 기록). 지운 칸 위치와
        값은 MANUAL 기록(management_delete)으로 남겨 현황표·변경 이력에 보인다.
        """
        ids = tuple(dict.fromkeys(int(i) for i in snapshot_ids))
        require(bool(ids), "지울 행을 골라 주세요.")
        with self.storage.transaction() as tx:
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
                                "cells": sorted(cells, key=_cell_order)})
            tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": {"schema": 1, "management_delete": {
                "table": "equipment_snapshot", "batch_id": batch_id, "sheet": sheet, "rows": removed}}})
        return len(removed)

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
        versions.sort(key=lambda v: (v.get("version_order") is None, v.get("version_order") or 0,
                                     _text(v.get("released_at")), v["release_version_id"]))
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
                name = (_text(values.get("group_name")) if table == "equipment_group" else _text(values.get("site"))
                        if table == "site" else
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
                        row.get("equipment_code") or "설비", "현황표 행", before, "삭제",
                        _workbook(raws.get(source)), removed.get("sheet"),
                        f"{cells[0]}~{cells[-1]}" if len(cells) > 1 else (cells[0] if cells else None)))
            if len(result) >= limit:
                break
        return tuple(result[:limit])
