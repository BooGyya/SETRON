"""검토를 마친 Excel 가져오기 묶음을 DB에 저장한다 (Jira S15P31B106-75).

가져오기 화면의 :class:`SaveRequest`를 받아 입력 작업(`import_batch`)과 설비 현황 또는 릴리즈·Item을
한 트랜잭션으로 저장한다. 하나라도 실패하면 묶음 전체를 취소한다.

연결 규칙 (이름으로 추정하지 않는다)
- 설비군은 Poly·Oxide·KCTC·HPA 표기만 분류로 바꾼다. 비어 있으면 확정된 모델 소속으로 정한다.
- 모델은 같은 분류 안에서 이름이 정확히 같은 등록 모델 하나만 쓴다. 별칭은 M5 → M-O5만 쓴다.
- 계열은 모델(또는 그 기본 모델)이 관리하는 계열 중 이름이 정확히 같은 것, 없으면 "이름 + 공백"으로
  시작하는 계열을 후보로 두고 UI 구분으로 하나를 고른다. 하나로 정해지지 않으면 저장하지 않는다.
- 설비 적용 버전은 같은 계열의 같은 버전 원문 릴리즈가 있을 때만 연결하고, 없으면 미등록으로 둔다.

이번 범위에서 하지 않는 것
- 같은 호기·기준일 현황이나 같은 계열·버전 릴리즈가 이미 있을 때의 교체·재가져오기 (BE-009·010·025)
- 릴리즈 노트 문서(`release_note`) 등록과 Item 대상 모델 범위 지정. Item은 APPLIED·범위 미확인으로 연결한다.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from setron.features.ai.schemas import ExtractKind
from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import DuplicateSaveError, SaveReceipt, SaveRequest, SaveRow
from setron.features.release.schemas import ItemInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.storage.service import StorageService
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import Database

CATEGORIES = {"poly": "POLY", "oxide": "OXIDE", "kctc": "KCTC", "hpa": "HPA"}
UI_TYPES = {"구ui": "LEGACY", "legacy": "LEGACY", "통합ui": "INTEGRATED", "integrated": "INTEGRATED"}
MODEL_CATEGORIES = {
    "C3": "POLY", "M-C3": "POLY", "C4": "POLY", "M-C4": "POLY",
    "M-O2": "OXIDE", "M-O3": "OXIDE", "M-O5": "OXIDE", "M-O5 Prime": "OXIDE",
    "Inline O2": "KCTC", "Cluster O2": "KCTC", "HPA": "HPA",
}
MODEL_ALIASES = {"M5": "M-O5"}

SNAPSHOT_FIELDS = ("base_date", "site", "line", "model_name", "equipment_code", "production_area",
                   "structure_type", "geometry_type", "modification_type", "group_name")
SOFTWARE_FIELDS = ("family_name", "version_raw", "ui_type")
PM_FIELDS = ("pm_name", "model_name")
RELEASE_FIELDS = ("version_raw", "release_date", "family_name", "model_name", "group_name", "ui_type")
ITEM_FIELDS = ("item_no", "content")
# 검토 화면에서 고친 항목 중 DB 수정 표시(edited_fields)에 남길 수 있는 것과 그 열 이름.
RELEASE_EDITED = {"version_raw": "version_raw", "release_date": "released_at"}
ITEM_EDITED = {"item_no": "item_no", "content": "content"}

TARGET_LABEL = "MySQL DB · 조회 화면에는 아직 반영되지 않음"


class DatabaseImportSaver:
    """가져오기 화면의 ``ImportSaver`` 구현. ``storage``는 공통 :class:`StorageService`다."""

    target_label = TARGET_LABEL

    def __init__(self, storage=None, *, connect: Callable[[], Any] | None = None) -> None:
        """``storage``를 바로 주거나, 첫 저장 때 ``connect()``로 만든다 (앱 시작 시 DB에 접속하지 않기 위함)."""
        _require(storage is not None or connect is not None, "저장 서비스 또는 접속 방법이 필요합니다.")
        self._storage, self._connect = storage, connect
        self.repository = EquipmentRepository()
        self._saved_requests: set[str] = set()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DatabaseImportSaver | None:
        """``SETRON_DB_*`` 접속 설정이 있으면 DB 저장소를 만든다. 설정이 없으면 None (메모리 저장 유지)."""
        values = os.environ if environ is None else environ
        if not values.get("SETRON_DB_NAME"):
            return None
        return cls(connect=lambda: StorageService(Database.from_env(values)))

    @property
    def storage(self):
        if self._storage is None:
            self._storage = self._connect()
        return self._storage

    def save(self, request: SaveRequest) -> SaveReceipt:
        """묶음 전체를 저장하고 입력 작업 번호를 돌려준다.

        raises
        - DuplicateSaveError: 같은 요청 번호를 이 화면에서 이미 저장한 경우.
        - StorageValidationError: 모델·계열 연결이 정해지지 않았거나 이미 저장된 현황·릴리즈가 있는 경우.
        - DatabaseConnectionError·DatabaseSaveError·DatabaseCommitUnknownError: DB 오류.
        """
        if request.request_id in self._saved_requests:
            raise DuplicateSaveError("이미 저장한 요청입니다.")
        _require(bool(request.rows), "저장할 행이 없습니다.")
        storage = self.storage
        equipment, releases = EquipmentService(storage), ReleaseService(storage)
        with storage.transaction() as tx:
            batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": batch_raw_data(request.raw_data)})
            if ExtractKind(request.kind) is ExtractKind.EQUIPMENT_STATUS:
                snapshots = self._snapshots(tx, request.rows, batch["batch_id"])
                equipment.save_snapshots_in(tx, snapshots)
            else:
                for release, items in self._releases(tx, request.rows, batch["batch_id"]):
                    releases.register_release_in(tx, release, items)
        self._saved_requests.add(request.request_id)
        return SaveReceipt(batch["batch_id"], len(request.rows), datetime.now(), self.target_label)

    # --- 설비 현황 -----------------------------------------------------------------

    def _snapshots(self, tx, rows: Sequence[SaveRow], batch_id: int) -> tuple[SnapshotInput, ...]:
        groups: dict[tuple[str, str], list[SaveRow]] = {}
        for row in rows:
            code, base = row.values.get("equipment_code"), row.values.get("base_date")
            _require(bool(code) and bool(base), f"{_where(row)}: 설비 호기와 기준일이 필요합니다.")
            groups.setdefault((code, base), []).append(row)
        return tuple(self._snapshot(tx, group, batch_id) for group in groups.values())

    def _snapshot(self, tx, rows: list[SaveRow], batch_id: int) -> SnapshotInput:
        first = rows[0].values
        code = first["equipment_code"]
        existing = self.repository.find_equipment(tx, code)
        if existing is not None:
            _require(not self.repository.rows(tx, "equipment_snapshot", equipment_id=existing["equipment_id"],
                                              base_date=date.fromisoformat(first["base_date"])),
                     f"{code}: {first['base_date']} 현황이 이미 저장돼 있습니다. 재가져오기는 아직 지원하지 않습니다.")
        models: dict[str, dict] = {}
        software: dict[tuple[int, str], dict[str, Any]] = {}
        for row in rows:
            values = row.values
            model = self._model(tx, values, row)
            models[model["group_name"]] = model
            family = self._family(tx, model, values, row)
            version = values.get("version_raw")
            _require(bool(version), f"{_where(row)}: SW 버전이 필요합니다.")
            entry = software.setdefault((family["family_id"], version), {
                "release": self._release_id(tx, family["family_id"], version),
                "sources": _sources(row, SOFTWARE_FIELDS), "pms": []})
            if values.get("pm_name"):
                entry["pms"].append(PmInput(pm_name=values["pm_name"], group_id=model["group_id"],
                                            model_name_raw=values.get("model_name"),
                                            source_reference=_sources(row, PM_FIELDS)))
        representative = next(iter(models.values()))["group_id"] if len(models) == 1 else None
        return SnapshotInput(
            equipment_code=code,
            base_date=date.fromisoformat(first["base_date"]),
            site=first.get("site"),
            model_name=first.get("model_name"),
            batch_id=batch_id,
            line=first.get("line"),
            group_id=representative,
            production_area=first.get("production_area"),
            structure_type=first.get("structure_type"),
            geometry_type=first.get("geometry_type"),
            modification_type=first.get("modification_type"),
            source_reference=_sources(rows[0], SNAPSHOT_FIELDS),
            software=tuple(SoftwareInput(family_id=family_id, version_raw=version, release_version_id=entry["release"],
                                         source_reference=entry["sources"], pms=tuple(entry["pms"]))
                           for (family_id, version), entry in software.items()),
        )

    # --- 릴리즈 노트 ---------------------------------------------------------------

    def _releases(self, tx, rows: Sequence[SaveRow], batch_id: int):
        groups: dict[tuple[int, str], list[SaveRow]] = {}
        for row in rows:
            version = row.values.get("version_raw")
            _require(bool(version), f"{_where(row)}: 릴리즈 버전이 필요합니다.")
            _require(bool(row.values.get("family_name")),
                     f"{_where(row)}: SW 계열이 없어 릴리즈를 연결할 수 없습니다. 검토 화면에서 SW 계열을 입력해 주세요.")
            model = self._model(tx, row.values, row)
            family = self._family(tx, model, row.values, row)
            groups.setdefault((family["family_id"], version), []).append(row)
        result = []
        for (family_id, version), group in groups.items():
            _require(self._release_id(tx, family_id, version) is None,
                     f"{version}: 같은 계열에 이미 저장된 릴리즈입니다. 릴리즈 교체는 아직 지원하지 않습니다.")
            first = group[0]
            dates = {r.values.get("release_date") for r in group} - {None}
            _require(len(dates) <= 1, f"{version}: 출시일이 행마다 다릅니다.")
            release = ReleaseInput(
                family_id=family_id, version_raw=version, batch_id=batch_id,
                released_at=date.fromisoformat(dates.pop()) if dates else None,
                source_reference=_sources(first, RELEASE_FIELDS),
                edited_fields=_edited(first, RELEASE_EDITED),
            )
            items, seen = [], set()
            for row in group:
                content = row.values.get("content")
                if not content:
                    continue
                key = (row.values.get("item_no"), content)
                if key in seen:
                    continue
                seen.add(key)
                items.append(ItemInput(content=content, batch_id=batch_id, item_no=row.values.get("item_no"),
                                       source_reference=_sources(row, ITEM_FIELDS),
                                       edited_fields=_edited(row, ITEM_EDITED)))
            result.append((release, tuple(items)))
        return result

    # --- 연결 ----------------------------------------------------------------------

    def _model(self, tx, values: Mapping[str, Any], row: SaveRow) -> dict:
        raw = (values.get("model_name") or "").strip()
        name = MODEL_ALIASES.get(raw, raw)
        category = _category(values.get("group_name"), name, row)
        _require(bool(name), f"{_where(row)}: 모델명이 필요합니다.")
        matches = [g for g in self.repository.rows(tx, "equipment_group", category=category) if g["group_name"] == name]
        _require(len(matches) == 1, f"{_where(row)}: 모델 '{raw}'({category})에 대응하는 등록 모델이 없거나 여러 개입니다.")
        return matches[0]

    def _family(self, tx, model: dict, values: Mapping[str, Any], row: SaveRow) -> dict:
        name = (values.get("family_name") or "").strip()
        _require(bool(name), f"{_where(row)}: SW 계열이 필요합니다.")
        ui_raw = values.get("ui_type")
        ui = UI_TYPES.get((ui_raw or "").replace(" ", "").casefold())
        _require(not ui_raw or ui is not None, f"{_where(row)}: UI 구분 '{ui_raw}'을(를) 확인해 주세요.")
        owners = {model["group_id"]} | ({model["parent_group_id"]} if model.get("parent_group_id") else set())
        families = [f for owner in sorted(owners) for f in self.repository.rows(tx, "version_family", group_id=owner)]
        candidates = [f for f in families if f["family_name"] == name] or [
            f for f in families if f["family_name"].startswith(name + " ")]
        if len(candidates) > 1 and ui:
            candidates = [f for f in candidates if f["ui_type"] == ui]
        _require(len(candidates) == 1,
                 f"{_where(row)}: 모델 '{model['group_name']}'의 SW 계열 '{name}'"
                 + (f"({ui_raw})" if ui_raw else "") + "을(를) 하나로 정할 수 없습니다.")
        return candidates[0]

    def _release_id(self, tx, family_id: int, version: str) -> int | None:
        # version_raw는 대소문자·공백까지 구분하는 이진 열이라 계열로 읽은 뒤 원문을 그대로 비교한다.
        rows = [r for r in self.repository.rows(tx, "release_version", family_id=family_id) if r["version_raw"] == version]
        return rows[0]["release_version_id"] if rows else None


# --- 형식 변환 --------------------------------------------------------------------

def batch_raw_data(raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """가져오기 원본(``{sheet, cells, merged}``)을 입력 작업 형식(``{시트: {셀: 값}}``)으로 바꾼다.

    병합 범위는 입력 작업 형식에 자리가 없어 넣지 않는다. 병합 셀의 값은 왼쪽 위 셀에 남아 있다.
    """
    _require(isinstance(raw, Mapping) and isinstance(raw.get("sheet"), str) and isinstance(raw.get("cells"), Mapping),
             "원본 시트 자료가 올바르지 않습니다.")
    return {raw["sheet"]: dict(raw["cells"])}


def _sources(row: SaveRow, names: Sequence[str]) -> dict | None:
    """필드별 ``{sheet, cells: "A1,B2"}`` 출처를 저장 형식 ``{sheet, cells: [...]}``으로 바꾼다."""
    result = {}
    for name in names:
        ref = (row.source_reference or {}).get(name)
        if ref:
            result[name] = {"sheet": ref["sheet"], "cells": [c for c in str(ref["cells"]).split(",") if c]}
    return result or None


def _edited(row: SaveRow, columns: Mapping[str, str]) -> tuple[str, ...]:
    """검토 화면에서 고친 항목을 DB 열 이름으로 바꾼다. 계열·모델처럼 연결에만 쓴 항목은 남기지 않는다."""
    return tuple(dict.fromkeys(columns[name] for name in row.edited_fields if name in columns))


def _category(raw: str | None, model: str, row: SaveRow) -> str:
    if raw:
        category = CATEGORIES.get(raw.strip().casefold()) or (raw.strip() if raw.strip() in CATEGORIES.values() else None)
        _require(category is not None, f"{_where(row)}: 설비군 '{raw}'을(를) Poly·Oxide·KCTC·HPA 중에서 확인해 주세요.")
        expected = MODEL_CATEGORIES.get(model)
        _require(expected is None or expected == category, f"{_where(row)}: 설비군 '{raw}'과 모델 '{model}'의 소속이 다릅니다.")
        return category
    category = MODEL_CATEGORIES.get(model)
    _require(category is not None, f"{_where(row)}: 설비군이 없어 모델 '{model}'의 분류를 정할 수 없습니다.")
    return category


def _where(row: SaveRow) -> str:
    return "검토 행 " + ", ".join(str(i) for i in row.row_ids)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StorageValidationError(message)
