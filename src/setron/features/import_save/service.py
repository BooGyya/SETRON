"""검토를 마친 Excel 가져오기 묶음을 DB에 저장한다 (Jira S15P31B106-75).

가져오기 화면의 :class:`SaveRequest`를 받아 입력 작업(`import_batch`)과 설비 현황 또는 릴리즈·Item을
한 트랜잭션으로 저장한다. 하나라도 실패하면 묶음 전체를 취소한다.

연결 규칙 (이름으로 추정하지 않는다)
- 설비군은 Poly·Oxide·KCTC·HPA 표기만 분류로 바꾼다. 비어 있으면 확정된 모델 소속으로 정한다.
- 모델은 같은 분류 안에서 이름이 정확히 같은 등록 모델 하나만 쓴다. 별칭은 M5 → M-O5만 쓴다.
- 계열은 모델(또는 그 기본 모델)이 관리하는 계열 중 이름이 정확히 같은 것, 없으면 "이름 + 공백"으로
  시작하는 계열을 후보로 두고 UI 구분으로 하나를 고른다. 하나로 정해지지 않으면 저장하지 않는다.
- 검토에서 확인한 계열·릴리즈 ID는 저장 시 DB에서 다시 확인한다. 릴리즈 ID가 없으면 같은 계열의
  같은 버전 원문만 연결하며, 대응이 없으면 미등록으로 둔다. 원문은 연결 ID와 별도로 보존한다.

이번 범위에서 하지 않는 것
- 같은 계열·버전 릴리즈 교체 (BE-010)와 저장 후 직접 수정 (BE-025)
- 릴리즈 노트 문서(`release_note`) 등록과 Item 대상 모델 범위 지정. Item은 APPLIED·범위 미확인으로 연결한다.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from threading import Lock

from setron.features.ai.schemas import ExtractKind
from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import (
    DuplicateSaveError, ImportDraft, ReviewRow, SaveReceipt, SaveRequest, SaveRow, SheetSnapshot,
)
from setron.features.excel_import.repository import ImportValidationRepository
from setron.features.excel_import.service import validate_batch
from setron.features.release.schemas import ItemInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.storage.service import StorageService
from setron.features.storage.inputs import identifier, merge_sources
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
SNAPSHOT_METADATA_FIELDS = ("line", "production_area", "structure_type", "geometry_type", "modification_type")
SOFTWARE_FIELDS = ("family_name", "version_raw", "ui_type")
PM_FIELDS = ("pm_name", "model_name")
RELEASE_FIELDS = ("version_raw", "release_date", "family_name", "model_name", "group_name", "ui_type")
ITEM_FIELDS = ("item_no", "content")
# 검토 화면에서 고친 항목 중 DB 수정 표시(edited_fields)에 남길 수 있는 것과 그 열 이름.
RELEASE_EDITED = {"version_raw": "version_raw", "release_date": "released_at"}
ITEM_EDITED = {"item_no": "item_no", "content": "content"}

TARGET_LABEL = "MySQL DB · 대시보드에서 조회 적용 후 반영"


class DatabaseImportSaver:
    """가져오기 화면의 ``ImportSaver`` 구현. ``storage``는 공통 :class:`StorageService`다."""

    target_label = TARGET_LABEL

    def __init__(self, storage=None, *, connect: Callable[[], Any] | None = None) -> None:
        """``storage``를 바로 주거나, 첫 저장 때 ``connect()``로 만든다 (앱 시작 시 DB에 접속하지 않기 위함)."""
        _require(storage is not None or connect is not None, "저장 서비스 또는 접속 방법이 필요합니다.")
        self._storage, self._connect = storage, connect
        self._storage_lock = Lock()
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
            with self._storage_lock:
                if self._storage is None:
                    self._storage = self._connect()
        return self._storage

    def save(self, request: SaveRequest) -> SaveReceipt:
        """검토·파일명 확인을 마친 한 시트의 묶음을 원자적으로 저장한다."""
        return self.save_many((request,))[0]

    def load_reference(self, drafts):
        """쓰기 없이 최신 DB 기존값을 읽어 검토 화면에 전달한다."""
        with self.storage.transaction() as tx:
            return ImportValidationRepository().load(tx, drafts)

    def save_many(self, requests: tuple[SaveRequest, ...]) -> tuple[SaveReceipt, ...]:
        """전체 검토 요청을 한 번에 저장한다. 후속 시트 실패도 앞선 변경과 함께 취소한다.

        기존 현황은 검토 당시의 값과 같은 경우에만 갱신한다. 검토 후 변경되었거나
        DB 기존값 검토 없이 보낸 재가져오기는 전체를 보류하고 재검토를 요청한다.
        """
        _require(isinstance(requests, tuple) and bool(requests), "저장할 요청 묶음이 필요합니다.")
        ids = [request.request_id for request in requests]
        if len(set(ids)) != len(ids) or self._saved_requests.intersection(ids):
            raise DuplicateSaveError("이미 저장했거나 중복된 요청입니다.")
        _require(all(request.rows for request in requests), "저장할 행이 없습니다.")
        storage = self.storage
        equipment, releases = EquipmentService(storage), ReleaseService(storage)
        receipts = []
        with storage.transaction() as tx:
            # 전체 요청의 검토 기준을 쓰기 전에 확인한다. 같은 설비 잠금 순서도 일정하게 유지한다.
            codes = sorted({row.values["equipment_code"] for request in requests
                            if ExtractKind(request.kind) is ExtractKind.EQUIPMENT_STATUS for row in request.rows
                            if isinstance(row.values.get("equipment_code"), str)})
            for code in codes:
                self.repository.find_equipment(tx, code, lock=True)
            for request in requests:
                self._check_review(tx, request)
            drafts = tuple(_validation_draft(request) for request in requests)
            reference = ImportValidationRepository().load(tx, drafts)
            errors = [issue for issue in validate_batch(drafts, reference) if issue.blocks_save]
            _require(not errors, "이번 저장 묶음 전체를 보류합니다. " + " / ".join(i.message for i in errors[:5]))
            prepared = []
            for request in requests:
                batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": batch_raw_data(request.raw_data)})
                snapshots = (self._snapshots(tx, request.rows, batch["batch_id"])
                             if ExtractKind(request.kind) is ExtractKind.EQUIPMENT_STATUS else ())
                prepared.append((request, batch, snapshots))
            metadata_anchors = _metadata_anchors(tuple(snapshots for _, _, snapshots in prepared))
            _check_release_links(tuple(snapshots for _, _, snapshots in prepared))
            for index, (request, batch, snapshots) in enumerate(prepared):
                if ExtractKind(request.kind) is ExtractKind.EQUIPMENT_STATUS:
                    # 앞선 요청이 새 릴리즈를 등록했다면 원문이 정확히 같은 연결도 확인한다.
                    snapshots = tuple(replace(value, software=tuple(replace(sw,
                        release_version_id=self._release_id(tx, sw.family_id, sw.version_raw, sw.release_version_id))
                        for sw in value.software)) for value in snapshots)
                    equipment.import_snapshots_in(tx, snapshots, metadata_keys={
                        key for key, owner in metadata_anchors.items() if owner == index
                    })
                else:
                    for release, items in self._releases(tx, request.rows, batch["batch_id"]):
                        releases.register_release_in(tx, release, items)
                receipts.append(SaveReceipt(batch["batch_id"], len(request.rows), datetime.now(), self.target_label))
        self._saved_requests.update(ids)
        return tuple(receipts)

    def _check_review(self, tx, request):
        if ExtractKind(request.kind) is not ExtractKind.EQUIPMENT_STATUS:
            return
        expected = {(s.equipment_code, s.base_date): s for s in request.expected_snapshots or ()}
        keys = {(r.values.get("equipment_code"), r.values.get("base_date")) for r in request.rows}
        for code, base in sorted(keys, key=str):
            try:
                base_date = date.fromisoformat(base)
            except (TypeError, ValueError):
                raise StorageValidationError("기준 날짜를 확인해 주세요.") from None
            equipment = self.repository.find_equipment(tx, code, lock=True) if isinstance(code, str) else None
            rows = self.repository.rows(tx, "equipment_snapshot", lock=True,
                equipment_id=equipment["equipment_id"], base_date=base_date) if equipment else []
            current = self.repository.load(tx, equipment, rows[0], lock=True) if rows else None
            _require(current is None or request.expected_snapshots is not None,
                     "이미 저장된 현황입니다. 기존값을 불러와 검토한 뒤 다시 저장해 주세요.")
            if request.expected_snapshots is not None:
                _require(current == expected.get((code, base_date)),
                         "검토 후 기존 현황이 변경되었습니다. 최신 기존값을 다시 불러와 검토해 주세요.")

    # --- 설비 현황 -----------------------------------------------------------------

    def _snapshots(self, tx, rows: Sequence[SaveRow], batch_id: int) -> tuple[SnapshotInput, ...]:
        groups: dict[tuple[str, str], list[SaveRow]] = {}
        for row in rows:
            code, base = row.values.get("equipment_code"), row.values.get("base_date")
            _require(bool(code) and bool(base), f"{_where(row)}: 설비 호기와 기준일이 필요합니다.")
            groups.setdefault((code, base), []).append(row)
        return tuple(self._snapshot(tx, group, batch_id) for group in groups.values())

    def _snapshot(self, tx, rows: list[SaveRow], batch_id: int) -> SnapshotInput:
        """같은 호기·날짜의 검토 행을 SW·PM별 현황으로 구성한다.

        param
        - tx: 묶음 전체를 저장하는 공통 트랜잭션.
        - rows: 같은 호기·날짜의 원문·출처·명시 연결을 가진 저장 요청 행.
        - batch_id: 이번 Excel 입력 작업 ID.

        return
        - 모든 행의 출처와 확인된 릴리즈 연결을 보존한 현황 입력.

        raises
        - StorageValidationError: 기존 현황이 있거나 모델·계열·릴리즈 연결이 상충하는 경우.
        """
        first = rows[0].values
        code = first["equipment_code"]
        metadata = {}
        for name in SNAPSHOT_METADATA_FIELDS:
            values = {row.values.get(name) for row in rows} - {None}
            _require(len(values) <= 1, f"{code}: 같은 날짜 현황의 {name} 값이 상충합니다.")
            metadata[name] = next(iter(values), None)
        models: dict[str, dict] = {}
        software: dict[tuple[int, str], dict[str, Any]] = {}
        for row in rows:
            values = row.values
            model = self._model(tx, values, row)
            models[model["group_name"]] = model
            family = self._family(tx, model, values, row)
            version = values.get("version_raw")
            _require(bool(version), f"{_where(row)}: SW 버전이 필요합니다.")
            release_id = self._release_id(tx, family["family_id"], version, row.release_version_id)
            key = family["family_id"], version
            entry = software.setdefault(key, {"release": release_id, "sources": None, "pms": []})
            _require(entry["release"] == release_id,
                     f"{_where(row)}: 같은 적용 SW의 릴리즈 연결이 상충합니다.")
            entry["sources"] = merge_sources(entry["sources"], _sources(row, SOFTWARE_FIELDS))
            if values.get("pm_name"):
                entry["pms"].append(PmInput(pm_name=values["pm_name"], group_id=model["group_id"],
                                            model_name_raw=values.get("model_name"),
                                            source_reference=_sources(row, (*PM_FIELDS, *SOFTWARE_FIELDS))))
        representative = next(iter(models.values()))["group_id"] if len(models) == 1 else None
        return SnapshotInput(
            equipment_code=code,
            base_date=date.fromisoformat(first["base_date"]),
            site=first.get("site"),
            model_name=first.get("model_name"),
            batch_id=batch_id,
            group_id=representative,
            **metadata,
            source_reference=merge_sources(*(_sources(row, SNAPSHOT_FIELDS) for row in rows)),
            software=tuple(SoftwareInput(family_id=family_id, version_raw=version, release_version_id=entry["release"],
                                         source_reference=entry["sources"], pms=tuple(entry["pms"]))
                           for (family_id, version), entry in software.items()),
        )

    # --- 릴리즈 노트 ---------------------------------------------------------------

    def _releases(self, tx, rows: Sequence[SaveRow], batch_id: int):
        """계열·버전별 검토 행을 릴리즈와 Item 등록 입력으로 구성한다.

        param
        - tx: 묶음 전체를 저장하는 공통 트랜잭션.
        - rows: 원문·출처·명시 연결을 가진 릴리즈 저장 요청 행.
        - batch_id: 이번 Excel 입력 작업 ID.

        return
        - 모든 출처와 수정 항목을 보존한 릴리즈·Item 등록 입력 목록.

        raises
        - StorageValidationError: 명시 연결이 상충하거나 같은 계열의 릴리즈가 이미 존재하는 경우.
        """
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
            linked_ids = {self._release_id(tx, family_id, version, row.release_version_id) for row in group}
            _require(len(linked_ids) == 1, f"{version}: 같은 릴리즈의 검토 연결이 상충합니다.")
            _require(next(iter(linked_ids)) is None,
                     f"{version}: 같은 계열에 이미 저장된 릴리즈입니다. 릴리즈 교체는 아직 지원하지 않습니다.")
            dates = {r.values.get("release_date") for r in group} - {None}
            _require(len(dates) <= 1, f"{version}: 출시일이 행마다 다릅니다.")
            release = ReleaseInput(
                family_id=family_id, version_raw=version, batch_id=batch_id,
                released_at=date.fromisoformat(dates.pop()) if dates else None,
                source_reference=merge_sources(*(_sources(row, RELEASE_FIELDS) for row in group)),
                edited_fields=tuple(dict.fromkeys(name for row in group for name in _edited(row, RELEASE_EDITED))),
            )
            items, seen = [], {}
            for row in group:
                content = row.values.get("content")
                if not content:
                    continue
                key = (row.values.get("item_no"), content)
                if key in seen:
                    index = seen[key]
                    previous = items[index]
                    items[index] = replace(previous,
                        source_reference=merge_sources(previous.source_reference, _sources(row, ITEM_FIELDS)),
                        edited_fields=tuple(dict.fromkeys((*previous.edited_fields, *_edited(row, ITEM_EDITED)))))
                    continue
                seen[key] = len(items)
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
        """모델 소유 범위·계열명·UI와 명시한 검토 ID가 모두 일치하는 계열을 찾는다.

        param
        - tx: 현재 저장 트랜잭션.
        - model: 입력 모델에 대응하는 등록 모델 행.
        - values: 계열명·UI 등 정규화한 저장값.
        - row: 선택한 계열 ID를 포함한 저장 요청 행. ID가 없으면 이름으로만 확인.

        return
        - 입력 모델 또는 부모 모델이 관리하는 유일한 일치 계열.

        raises
        - StorageValidationError: UI·소유 범위·계열명·명시 ID가 불일치하거나 후보가 여러 개인 경우.
        """
        name = (values.get("family_name") or "").strip()
        _require(bool(name), f"{_where(row)}: SW 계열이 필요합니다.")
        ui_raw = values.get("ui_type")
        ui = UI_TYPES.get((ui_raw or "").replace(" ", "").casefold())
        _require(not ui_raw or ui is not None, f"{_where(row)}: UI 구분 '{ui_raw}'을(를) 확인해 주세요.")
        owners = {model["group_id"]} | ({model["parent_group_id"]} if model.get("parent_group_id") else set())
        families = [f for owner in sorted(owners) for f in self.repository.rows(tx, "version_family", group_id=owner)]
        # 정확한 계열명이 다른 UI의 후보를 가리켜도 올바른 UI의 접두 이름 후보를 버리지 않는다.
        if ui:
            families = [family for family in families if family["ui_type"] == ui]
        candidates = [f for f in families if f["family_name"] == name] or [
            f for f in families if f["family_name"].startswith(name + " ")]
        _require(len(candidates) == 1,
                 f"{_where(row)}: 모델 '{model['group_name']}'의 SW 계열 '{name}'"
                 + (f"({ui_raw})" if ui_raw else "") + "을(를) 하나로 정할 수 없습니다.")
        if row.family_id is not None:
            identifier(row.family_id, "family_id")
            _require(candidates[0]["family_id"] == row.family_id,
                     f"{_where(row)}: 검토한 계열 ID와 현재 모델·계열·UI의 대응이 다릅니다.")
        return candidates[0]

    def _release_id(self, tx, family_id: int, version: str, release_version_id: int | None = None) -> int | None:
        """명시적인 검토 연결을 확인하거나 같은 계열의 정확한 원문 릴리즈를 찾는다.

        param
        - tx: 현재 저장 트랜잭션.
        - family_id: 적용 SW 또는 릴리즈의 계열 ID.
        - version: 그대로 보존할 버전 원문.
        - release_version_id: 검토에서 확인한 연결 ID. None이면 정확한 원문만 조회.

        return
        - 같은 계열에서 확인한 릴리즈 ID. 명시 ID와 정확한 원문 대응이 모두 없으면 None.

        raises
        - StorageValidationError: 명시 ID가 잘못됐거나 없거나 다른 계열을 가리키는 경우.
        """
        if release_version_id is not None:
            identifier(release_version_id, "release_version_id")
            try:
                release = tx.get("release_version", {"release_version_id": release_version_id})
            except LookupError as exc:
                raise StorageValidationError("검토한 릴리즈 ID가 현재 등록 자료에 없습니다.") from exc
            _require(release["family_id"] == family_id, "검토한 릴리즈 ID의 버전 계열이 적용 SW와 다릅니다.")
            return release_version_id
        # 원문은 대소문자·공백까지 구분하며 확인되지 않은 별칭을 추정하지 않는다.
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
    """새 목록형·기존 CSV형 출처를 공통 검증을 거친 저장 형식으로 변환한다.

    param
    - row: 필드별 원본 위치를 가진 저장 요청 행.
    - names: 이번 저장 대상에 연결할 공통 항목 이름.

    return
    - 시트별 셀 주소를 모두 보존한 공통 출처 객체. 출처가 없으면 None.

    raises
    - StorageValidationError: 시트·셀 목록의 형식이 잘못된 경우.
    """
    result = {}
    for name in names:
        ref = (row.source_reference or {}).get(name)
        if ref is not None:
            locations = ref if isinstance(ref, list) else [ref]
            converted = []
            for location in locations:
                _require(isinstance(location, dict), f"{name}: 원본 위치 객체가 필요합니다.")
                cells = location.get("cells")
                if isinstance(cells, str):
                    cells = [cell.strip() for cell in cells.split(",") if cell.strip()]
                converted.append({**location, "cells": cells})
            result[name] = converted
    return merge_sources(result or None)


def _check_release_links(groups: tuple[tuple[SnapshotInput, ...], ...]) -> None:
    """같은 현황·SW의 확정 릴리즈 연결이 요청 사이에서 상충하면 전체를 보류한다."""
    links = {}
    for snapshots in groups:
        for snapshot in snapshots:
            for software in snapshot.software:
                if software.release_version_id is None:
                    continue
                key = (snapshot.equipment_code, snapshot.base_date, software.family_id, software.version_raw)
                previous = links.setdefault(key, software.release_version_id)
                _require(previous == software.release_version_id,
                         f"{snapshot.equipment_code}: 같은 적용 SW의 릴리즈 연결이 요청 사이에서 상충합니다. "
                         "확인한 릴리즈를 하나로 맞춘 뒤 다시 저장해 주세요.")


def _metadata_anchors(groups: tuple[tuple[SnapshotInput, ...], ...]) -> dict[tuple[str, date], int]:
    """날짜별 공통정보가 모두 있는 한 시트의 입력 작업을 현황의 출처로 선택한다.

    다른 시트의 원본을 한 batch_id에 섞지 않는다. 공통정보가 여러 시트에 나뉘어
    어느 한 시트도 전체 값을 갖지 않으면 전체 저장을 보류한다.
    """
    members: dict[tuple[str, date], list[tuple[int, SnapshotInput]]] = {}
    for index, snapshots in enumerate(groups):
        for snapshot in snapshots:
            members.setdefault((snapshot.equipment_code, snapshot.base_date), []).append((index, snapshot))
    anchors = {}
    for key, snapshots in members.items():
        metadata = {}
        for name in SNAPSHOT_METADATA_FIELDS:
            values = {getattr(snapshot, name) for _, snapshot in snapshots} - {None}
            _require(len(values) <= 1, f"{key[0]}: 같은 날짜 현황의 {name} 값이 상충합니다.")
            metadata[name] = next(iter(values), None)
        candidates = [index for index, snapshot in snapshots
                      if all(getattr(snapshot, name) == value for name, value in metadata.items())]
        _require(bool(candidates),
                 f"{key[0]}: 같은 날짜의 공통 정보가 여러 시트에 나뉘어 있습니다. "
                 "라인·생산 영역·구조·형상·개조 정보를 한 시트에서 함께 확인한 뒤 다시 저장해 주세요.")
        anchors[key] = candidates[-1]
    return anchors


def _validation_draft(request: SaveRequest) -> ImportDraft:
    """저장 경계에서도 BE-008의 형식·필수값·행간 충돌 규칙을 다시 적용한다.

    현재값은 이미 검토·정규화한 값이다. 보관할 원본은 request.raw_data를 그대로 사용하며
    검증을 위해 만든 문자열 값으로 원본 셀을 덮어쓰지 않는다.
    """
    rows = []
    for index, row in enumerate(request.rows, 1):
        values = dict(row.values)
        if type(values.get("item_no")) is int:
            values["item_no"] = str(values["item_no"])
        rows.append(ReviewRow(index, {}, normalized=values))
    raw = request.raw_data
    sheet = SheetSnapshot(raw.get("sheet", ""), 1, 1, 1, 1, raw.get("cells", {}))
    return ImportDraft(ExtractKind(request.kind), request.workbook_name, sheet, tuple(rows),
                       workbook_id=request.workbook_id)


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
