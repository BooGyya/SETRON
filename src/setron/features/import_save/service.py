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

릴리즈 전체 교체는 파일명·전체 누적 노트 확인과 기존값 검토를 마친 요청만 처리한다.
원본 저장·기존 수동값 선택·위험 대상 보존·현재 목표 N 재검증은 같은 트랜잭션에 포함한다.
신규 Item은 APPLIED·모델 범위 미확인으로 연결하며, 릴리즈 노트 문서(`release_note`) 등록은 별도 작업이다.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from typing import Any
from threading import Lock

from setron.features.ai.schemas import ExtractKind
from setron.features.data_admin.service import WORKBOOK_KEY, with_workbook
from setron.features.reference_data import (
    FOUNDRY_FAMILY_MODELS, FOUNDRY_SUFFIX, MODEL_CATEGORIES as _REFERENCE_CATEGORIES, area_of, resolve_model_text,
    strip_foundry,
)
from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.service import EquipmentService
from setron.features.excel_import.schemas import (
    DuplicateSaveError, ImportDraft, ReviewRow, SaveReceipt, SaveRequest, SaveRow, SheetSnapshot,
)
from setron.features.excel_import.repository import ImportValidationRepository
from setron.features.excel_import.service import parse_address, validate_batch
from setron.features.excel_import.provenance import validate_save_request_sources
from setron.features.release.schemas import ItemInput, ReleaseInput
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.service import ReleaseService
from setron.features.release.refresh import ReleaseRefreshService
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.replacement import FamilyReplacementService
from setron.features.release.replacement_schemas import (
    ReplacementItem, ReplacementLink, ReplacementRelease, ReplacementRequest, ReplacementReview,
    ReplacementPreview, ReplacementReviewRequired,
)
from setron.features.storage.service import StorageService
from setron.features.storage.inputs import identifier, merge_sources
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import Database

CATEGORIES = {"poly": "POLY", "oxide": "OXIDE", "kctc": "KCTC", "hpa": "HPA"}
UI_TYPES = {"구ui": "LEGACY", "legacy": "LEGACY", "통합ui": "INTEGRATED", "integrated": "INTEGRATED"}
MODEL_CATEGORIES = dict(_REFERENCE_CATEGORIES)
MODEL_ALIASES = {"M5": "M-O5"}

SNAPSHOT_FIELDS = ("base_date", "site", "line", "model_name", "equipment_code", "production_area",
                   "structure_type", "geometry_type", "modification_type", "group_name", "process_name")
SNAPSHOT_METADATA_FIELDS = ("line", "production_area", "structure_type", "geometry_type", "modification_type")
SOFTWARE_FIELDS = ("family_name", "version_raw", "ui_type")
PM_FIELDS = ("pm_name", "model_name")
RELEASE_FIELDS = ("version_raw", "release_date", "family_name", "model_name", "group_name", "ui_type",
                  "production_area", "version_order", "version_suffix", "item_mapping_status")
ITEM_FIELDS = ("item_no", "content", "jira_code", "jira_key")
# 검토 화면에서 고친 항목 중 DB 수정 표시(edited_fields)에 남길 수 있는 것과 그 열 이름.
RELEASE_EDITED = {"version_raw": "version_raw", "release_date": "released_at", "version_suffix": "version_suffix",
                  "version_order": "version_order", "item_mapping_status": "item_mapping_status"}
ITEM_EDITED = {"item_no": "item_no", "content": "content", "jira_code": "jira_code", "jira_key": "jira_code"}

TARGET_LABEL = "MySQL DB"


@dataclass(frozen=True)
class ReleaseImportPlan:
    """실제 가져오기에서 표시할 계열 전체 교체 검토 데이터.

    ivar
    - request: 전체 누적 노트·파일명 확인을 마친 계열별 새 자료.
    - preview: 원본 식별·수정값·포함 관계·위험 복원 검토 결과.
    - family_name: 검토 화면에 표시할 계열 이름.
    - release_labels, item_labels: 기존 항목 ID별 실제 버전 원문·Item 설명.
    - risk_labels: 수동 위험 ID별 사유.
    - model_labels: 적용 대상 모델 ID별 이름.
    """

    request: ReplacementRequest
    preview: ReplacementPreview
    family_name: str = ""
    release_labels: dict[int, str] = field(default_factory=dict)
    item_labels: dict[int, str] = field(default_factory=dict)
    risk_labels: dict[int, str] = field(default_factory=dict)
    model_labels: dict[int, str] = field(default_factory=dict)


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

    def preview_release_import(self, request, reviews=()):
        """확인한 전체 릴리즈 노트의 계열별 충돌을 DB 쓰기 없이 조회한다.

        param
        - request: 한 SaveRequest 또는 함께 저장할 요청 튜플.
        - reviews: 앞 단계에서 고른 계열 순서의 매칭·충돌 선택. 처음에는 빈 튜플.

        return
        - 계열 ID 순서의 ReleaseImportPlan 튜플.
        """
        requests = request if isinstance(request, tuple) else (request,)
        self._remember_workbooks(requests)
        _check_confirmations(requests)
        for value in requests:
            validate_save_request_sources(value)
        with self.storage.transaction() as tx:
            replacements = self._replacement_requests(tx, requests)
            _require(not reviews or len(reviews) == len(replacements), "계열별 검토 선택 수가 다릅니다.")
            service = FamilyReplacementService(self.storage, _refresh_context(requests))
            result = []
            for index, value in enumerate(replacements):
                state = service._state(tx, value.family_id)
                family = tx.get("version_family", {"family_id": value.family_id})
                result.append(ReleaseImportPlan(value, service.preview_in(tx, value, reviews[index] if reviews else None),
                    family["family_name"],
                    {row["release_version_id"]: row["version_raw"] for row in state["releases"]},
                    {row["item_id"]: f"Item {row['item_no']} · {row['content']}" for row in state["items"]},
                    {row["risk_rule_id"]: row["risk_reason"] for row in state["risks"]},
                    {row["group_id"]: row["group_name"] for row in self.repository.rows(tx, "equipment_group")}))
            return tuple(result)

    def _management_links(self, tx, request):
        """앱 내보내기 표에 명시된 Item 상태·적용 모델만 복원한다. 일반 노트 기호는 추측하지 않는다."""
        from setron.features.management_format import RELEASE_HEADERS

        if ExtractKind(request.kind) is not ExtractKind.RELEASE_NOTE:
            return {}
        header_rows = {}
        for sheet_name, cells in batch_raw_data(request.raw_data).items():
            if not isinstance(cells, dict):
                continue
            for address, value in cells.items():
                row, _ = parse_address(address)
                if isinstance(value, str):
                    header_rows.setdefault((sheet_name, row), set()).add(value)
        if not any(set(RELEASE_HEADERS).issubset(values) for values in header_rows.values()):
            return {}
        groups = {row["group_name"]: row["group_id"] for row in self.repository.rows(tx, "equipment_group")}
        family_cache, links = {}, {}
        states = {"적용": ItemState.APPLIED, "미적용": ItemState.NOT_APPLIED, "미확인": ItemState.UNKNOWN}
        for row in request.rows:
            values = row.values
            if not values.get("content"):
                continue
            mark = values.get("modification_mark")
            _require(mark in states, "내보낸 Item의 적용·미적용·미확인 상태를 확인해 주세요.")
            targets = values.get("target_models")
            ids = ()
            if targets == "전체 모델":
                scope = TargetScope.ALL
            elif targets == "미확인":
                scope = TargetScope.UNKNOWN
            else:
                labels = tuple(part.strip() for part in (targets or "").split(",") if part.strip())
                _require(labels and all(label in groups for label in labels), "내보낸 Item의 지정 모델을 확인해 주세요.")
                scope, ids = TargetScope.LISTED, tuple(sorted({groups[label] for label in labels}))
            context = tuple(values.get(name) for name in ("model_name", "group_name", "family_name", "ui_type",
                                                          "structure_type", "production_area"))
            if context not in family_cache:
                family_cache[context] = self._family(tx, self._model(tx, values, row), values, row)["family_id"]
            key = family_cache[context], values["version_raw"], values.get("item_no"), values["content"]
            metadata = {"item_state": states[mark], "target_scope": scope, "target_group_ids": ids}
            _require(key not in links or links[key] == metadata, "같은 버전·Item의 적용 상태나 모델 범위가 상충합니다.")
            links[key] = metadata
        return links

    def _replacement_requests(self, tx, requests):
        """여러 시트를 같은 계열의 전체 노트로 묶고 각 원본 셀 위치를 보존한다.

        param
        - tx: 현재 읽기 또는 저장 업무.
        - requests: 전체 누적 노트·파일명 확인을 마친 SaveRequest 목록.

        return
        - 계열 ID 순서의 교체 요청. 같은 이름의 원본 셀 값이 다르면 저장하지 않음.
        """
        groups, raws, management_links = {}, {}, {}
        for request in requests:
            for key, metadata in self._management_links(tx, request).items():
                _require(key not in management_links or management_links[key] == metadata,
                         "같은 버전·Item의 적용 상태나 모델 범위가 시트마다 다릅니다.")
                management_links[key] = metadata
            if ExtractKind(request.kind) is not ExtractKind.RELEASE_NOTE or not request.release_note_confirmed:
                continue
            _require(request.filename_confirmed is True, "릴리즈 교체 전에 원본 파일명을 확인해 주세요.")
            raw = batch_raw_data(request.raw_data)
            for release, items in self._releases(tx, request.rows, None, allow_existing=True):
                family = release.family_id
                groups.setdefault(family, []).append((release, items))
                target = raws.setdefault(family, {})
                for sheet, cells in raw.items():
                    destination = target.setdefault(sheet, {})
                    _require(all(address not in destination or destination[address] == value
                                 for address, value in cells.items()),
                             "같은 시트명·셀 주소의 원본 값이 다릅니다. 한 계열의 전체 누적 노트를 같은 통합문서에서 검토해 주세요.")
                    destination.update(cells)
                if (request.workbook_name or "").strip():
                    target[WORKBOOK_KEY] = request.workbook_name.strip()  # 시트 합치기 뒤에 파일 이름만 더한다
        for request in requests:
            if ExtractKind(request.kind) is ExtractKind.RELEASE_NOTE and not request.release_note_confirmed:
                for row in request.rows:
                    family = self._family(tx, self._model(tx, row.values, row), row.values, row)["family_id"]
                    _require(family not in groups, "같은 계열의 일부 시트만 전체 교체로 확인할 수 없습니다. 모든 시트를 함께 검토해 주세요.")
        result = []
        for family, entries in sorted(groups.items()):
            matching = FamilyReplacementService(self.storage)
            state = matching._state(tx, family)
            merged = {}
            for release, items in entries:
                key = release.version_raw
                if key in merged:
                    previous, previous_items = merged[key]
                    _require(previous.released_at == release.released_at, "같은 릴리즈의 출시일이 시트마다 다릅니다.")
                    release = replace(previous, source_reference=merge_sources(previous.source_reference, release.source_reference),
                        edited_fields=tuple(dict.fromkeys((*previous.edited_fields, *release.edited_fields))))
                    items = (*previous_items, *items)
                merged[key] = release, items
            releases, all_items, shared = [], [], {}
            for ri, (release, items) in enumerate(merged.values()):
                seen, links = {}, []
                for item in items:
                    key = (item.item_no, item.content)
                    metadata = management_links.get((family, release.version_raw, *key), {})
                    if key in seen:
                        index = seen[key]
                        previous = all_items[index]
                        all_items[index] = replace(previous, value=replace(previous.value,
                            source_reference=merge_sources(previous.value.source_reference, item.source_reference),
                            edited_fields=tuple(dict.fromkeys((*previous.value.edited_fields, *item.edited_fields)))))
                        continue
                    existing_item = self._confirmed_item_match(matching, state, raws[family], release, item)
                    if existing_item is not None and existing_item in shared:
                        index = shared[existing_item]
                        previous = all_items[index]
                        _require((previous.value.item_no, previous.value.content) == (item.item_no, item.content),
                                 "같은 기존 Item에 대응하는 새 내용이 버전마다 다릅니다. 전체 노트에서 값을 확인해 주세요.")
                        all_items[index] = replace(previous, value=replace(previous.value,
                            source_reference=merge_sources(previous.value.source_reference, item.source_reference),
                            edited_fields=tuple(dict.fromkeys((*previous.value.edited_fields, *item.edited_fields)))))
                        seen[key] = index
                        links.append(ReplacementLink(item_key=previous.key, **metadata))
                        continue
                    item_key = f"release-{ri}-item-{len(links)}"
                    seen[key] = len(all_items)
                    if existing_item is not None:
                        shared[existing_item] = len(all_items)
                    all_items.append(ReplacementItem(key=item_key, value=item))
                    links.append(ReplacementLink(item_key=item_key, **metadata))
                releases.append(ReplacementRelease(key=f"release-{ri}", value=release, links=tuple(links)))
            result.append(ReplacementRequest(family_id=family, releases=tuple(releases), items=tuple(all_items),
                raw_data=raws[family], complete_note_confirmed=True, filename_confirmed=True))
        return tuple(result)

    @staticmethod
    def _confirmed_item_match(service, state, raw, release, item):
        """원본 버전·번호가 하나의 기존 Item을 확인한 경우에만 공유 키를 유지한다.

        param
        - service, state: 교체의 원본 조회 함수와 현재 계열 자료.
        - raw, release, item: 새 전체 노트 원본과 검토한 릴리즈·Item 값.

        return
        - 유일하게 확인한 기존 Item ID. 번호만 같거나 불확실하면 None.
        """
        batches = {row["batch_id"]: row["raw_data"] for row in state["batches"]}
        version = service._source(raw, asdict(release), "version_raw")
        number = service._number(service._source(raw, asdict(item), "item_no"))
        if version is None or number is None:
            return None
        old = [row for row in state["releases"]
               if service._source(batches[row["batch_id"]], row, "version_raw") == version]
        if len(old) != 1:
            return None
        ids = {row["item_id"] for row in state["links"] if row["release_version_id"] == old[0]["release_version_id"]}
        candidates = [row["item_id"] for row in state["items"] if row["item_id"] in ids
            and service._number(service._source(batches[row["batch_id"]], row, "item_no")) == number]
        return candidates[0] if len(candidates) == 1 else None

    def save_many(self, requests: tuple[SaveRequest, ...]) -> tuple[SaveReceipt, ...]:
        """전체 검토 요청을 한 번에 저장한다. 후속 시트 실패도 앞선 변경과 함께 취소한다.

        기존 현황은 검토 당시의 값과 같은 경우에만 갱신한다. 검토 후 변경되었거나
        DB 기존값 검토 없이 보낸 재가져오기는 전체를 보류하고 재검토를 요청한다.
        """
        _require(isinstance(requests, tuple) and bool(requests), "저장할 요청 묶음이 필요합니다.")
        self._remember_workbooks(requests)
        _check_confirmations(requests)
        ids = [request.request_id for request in requests]
        if len(set(ids)) != len(ids) or self._saved_requests.intersection(ids):
            raise DuplicateSaveError("이미 저장했거나 중복된 요청입니다.")
        _require(all(request.rows for request in requests), "저장할 행이 없습니다.")
        for request in requests:
            validate_save_request_sources(request)
        storage = self.storage
        equipment, releases = EquipmentService(storage), ReleaseService(storage)
        context = _refresh_context(requests)
        with storage.transaction() as discovery:
            release_families = sorted({self._family(discovery, self._model(discovery, row.values, row), row.values, row)["family_id"]
                for request in requests if ExtractKind(request.kind) is ExtractKind.RELEASE_NOTE for row in request.rows})
        receipts, receipt_batches, changed_release_ids = [], {}, set()
        with storage.transaction() as tx:
            for family_id in release_families:
                releases.repository.get(tx, "version_family", {"family_id": family_id}, lock=True)
            replacement_requests = self._replacement_requests(tx, requests)
            selected_reviews = [request.release_reviews for request in requests if request.release_reviews]
            _require(not selected_reviews or all(value == selected_reviews[0] for value in selected_reviews),
                     "같은 저장 묶음의 계열별 충돌 선택이 다릅니다.")
            reviews = selected_reviews[0] if selected_reviews else ()
            replacement_service = FamilyReplacementService(storage, context)
            if replacement_requests and len(reviews) != len(replacement_requests):
                first = replacement_requests[0]
                raise ReplacementReviewRequired(replacement_service.preview_in(tx, first))
            states = []
            for value, review in zip(replacement_requests, reviews):
                state = replacement_service._state(tx, value.family_id, lock=True)
                preview = replacement_service._preview(tx, value, state, review)
                if preview.questions or preview.conflicts or preview.link_conflicts:
                    raise ReplacementReviewRequired(preview)
                states.append(state)
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
            # 기존 번호 중복의 대응 선택은 전체 교체 검토에서 처리한다. 새 입력의 중복·필수값 오류는 유지한다.
            if replacement_requests:
                reference = replace(reference, stored_releases=())
            errors = [issue for issue in validate_batch(drafts, reference) if issue.blocks_save]
            _require(not errors, "이번 저장 묶음 전체를 보류합니다. " + " / ".join(i.message for i in errors[:5]))
            prepared = []
            for request in requests:
                if ExtractKind(request.kind) is ExtractKind.RELEASE_NOTE and request.release_note_confirmed:
                    continue
                batch = tx.insert("import_batch", {"source_type": "EXCEL",
                                                   "raw_data": batch_raw_data(request.raw_data, request.workbook_name)})
                receipt_batches[request.request_id] = (batch["batch_id"],)
                snapshots = (self._snapshots(tx, request.rows, batch["batch_id"])
                             if ExtractKind(request.kind) is ExtractKind.EQUIPMENT_STATUS else ())
                prepared.append((request, batch, snapshots))
            for value, review, expected in zip(replacement_requests, reviews, states):
                current = replacement_service._state(tx, value.family_id, lock=True)
                _require(all(current[key] == expected[key] for key in ("releases", "items", "risk_items", "risks", "batches")),
                         "검토 후 해당 계열의 관리값이 변경되었습니다. 다시 검토해 주세요.")
                # 앞선 계열 교체가 공유 Item의 외부 연결만 바꾼 경우 검토한 동일 대상·값 선택을 유지한다.
                current_preview = replacement_service.preview_in(tx, value)
                current_review = replace(review, fingerprint=current_preview.fingerprint)
                details, _ = replacement_service.replace_in(tx, value, current_review, refresh=False)
                batch_ids = tuple(dict.fromkeys(detail.release.current["batch_id"] for detail in details))
                changed_release_ids.update(detail.release.current["release_version_id"] for detail in details)
                for request in requests:
                    if ExtractKind(request.kind) is ExtractKind.RELEASE_NOTE and request.release_note_confirmed:
                        own = {self._family(tx, self._model(tx, row.values, row), row.values, row)["family_id"] for row in request.rows}
                        if value.family_id in own:
                            receipt_batches[request.request_id] = tuple(dict.fromkeys((*receipt_batches.get(request.request_id, ()), *batch_ids)))
            metadata_anchors = _metadata_anchors(tuple(snapshots for _, _, snapshots in prepared))
            _check_release_links(tuple(snapshots for _, _, snapshots in prepared))
            equipment_batches, equipment_metadata_keys = [], []
            for request, batch, snapshots in prepared:
                if ExtractKind(request.kind) is ExtractKind.RELEASE_NOTE:
                    management_links = self._management_links(tx, request)
                    for release, items in self._releases(tx, request.rows, batch["batch_id"]):
                        detail = releases.register_release_in(tx, release, items)
                        release_id = detail.release.current["release_version_id"]
                        for linked in detail.items:
                            item = linked.item.current
                            metadata = management_links.get((release.family_id, release.version_raw,
                                                             item["item_no"], item["content"]))
                            if metadata is not None:
                                tx.update("release_version_item", {"release_version_id": release_id, "item_id": item["item_id"]},
                                          {**metadata, "item_state": metadata["item_state"].value,
                                           "target_scope": metadata["target_scope"].value,
                                           "target_group_ids": list(metadata["target_group_ids"])
                                           if metadata["target_scope"] is TargetScope.LISTED else None})
                        changed_release_ids.add(detail.release.current["release_version_id"])
            for index, (request, batch, snapshots) in enumerate(prepared):
                if ExtractKind(request.kind) is ExtractKind.EQUIPMENT_STATUS:
                    # 함께 등록한 릴리즈를 확인한 뒤 원본 작업별 PM·출처를 유지해 적용한다.
                    snapshots = tuple(replace(value, software=tuple(replace(sw,
                        release_version_id=self._release_id(tx, sw.family_id, sw.version_raw, sw.release_version_id))
                        for sw in value.software)) for value in snapshots)
                    equipment_batches.append(snapshots)
                    equipment_metadata_keys.append({
                        key for key, owner in metadata_anchors.items() if owner == index
                    })
            if equipment_batches:
                equipment.import_snapshot_batches_in(tx, tuple(equipment_batches),
                    metadata_keys_by_batch=tuple(equipment_metadata_keys))
            refreshed = (ReleaseRefreshService().refresh_in(tx, context=context,
                family_ids=tuple(release_families), invalidated_applicability_release_ids=tuple(sorted(changed_release_ids)))
                if release_families else None)
            for request in requests:
                batch_ids = receipt_batches[request.request_id]
                receipts.append(SaveReceipt(batch_ids[0], len(request.rows), datetime.now(), self.target_label, refreshed))
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
        # 공정(11/13 멘토): 설비 하나에 하나이고 복합공정도 한 칸에 적는다. PM 줄마다 다르면 막지 않고 잇는다.
        processes = list(dict.fromkeys(str(row.values["process_name"]).strip() for row in rows
                                       if str(row.values.get("process_name") or "").strip()))
        metadata["process_name"] = " / ".join(processes)[:100] if processes else None
        models: dict[str, dict] = {}
        software: dict[tuple[int, str], dict[str, Any]] = {}
        for row in rows:
            values = row.values
            model = self._model(tx, values, row)
            models[model["group_name"]] = model
            family = self._family(tx, model, values, row, equipment=True)
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

    def _releases(self, tx, rows: Sequence[SaveRow], batch_id: int | None, *, allow_existing=False):
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
            _require(allow_existing or next(iter(linked_ids)) is None,
                     f"{version}: 이미 저장된 릴리즈입니다. 전체 누적 노트·파일명·충돌 선택을 확인한 뒤 교체해 주세요.")
            dates = {r.values.get("release_date") for r in group} - {None}
            orders = {r.values.get("version_order") for r in group} - {None, ""}
            _require(len(orders) <= 1, f"{version}: 확인된 릴리즈 순서가 상충합니다.")
            order = next(iter(orders), None)
            try:
                order = int(order) if order is not None else None
            except (TypeError, ValueError):
                raise StorageValidationError("확인된 순서는 정수여야 합니다.") from None
            suffixes = {r.values.get("version_suffix") for r in group} - {None, ""}
            _require(len(suffixes) <= 1, f"{version}: 릴리즈 접미사가 상충합니다.")
            mapping_labels = {r.values.get("item_mapping_status") for r in group} - {None, ""}
            mappings = {"확인됨": "VERIFIED", "미확인": "UNKNOWN", "VERIFIED": "VERIFIED", "UNKNOWN": "UNKNOWN"}
            _require(len(mapping_labels) <= 1 and all(value in mappings for value in mapping_labels),
                     "Item 매핑 확인 상태를 확인해 주세요.")
            # 같은 버전이 다른 날짜로 또 나오면 재배포로 보고 첫 출시일을 쓴다 (멘토 확인 2026-11-05).
            release = ReleaseInput(
                family_id=family_id, version_raw=version, batch_id=batch_id,
                version_suffix=next(iter(suffixes), None),
                released_at=date.fromisoformat(min(dates)) if dates else None,
                version_order=order,
                item_mapping_status=mappings[next(iter(mapping_labels))] if mapping_labels else "UNKNOWN",
                source_reference=merge_sources(*(_sources(row, RELEASE_FIELDS) for row in group)),
                edited_fields=tuple(dict.fromkeys(name for row in group for name in _edited(row, RELEASE_EDITED))),
            )
            items, seen = [], {}
            for row in group:
                content = row.values.get("content")
                if not content:
                    continue
                from setron.features.item.jira import extract_jira_from_source, join_codes, normalize_jira, split_codes
                try:
                    jira = extract_jira_from_source(getattr(self, "_row_raw_data", {}).get(id(row)),
                                                    _sources(row, ITEM_FIELDS), content=content, strict=False)
                    supplied = row.values.get("jira_code", row.values.get("jira_key"))
                    legacy = row.values.get("jira_key")
                    if supplied is not None and legacy is not None:
                        _require(normalize_jira(supplied) == normalize_jira(legacy),
                                 "같은 Item의 Jira 입력값이 상충합니다.")
                    edited_jira = any(name in row.edited_fields for name in ("jira_code", "jira_key"))
                    code = normalize_jira(supplied) if supplied is not None or edited_jira else jira.jira_code
                except ValueError as error:
                    raise StorageValidationError(str(error)) from error
                item_sources = merge_sources(_sources(row, ITEM_FIELDS), jira.source_reference)
                key = (row.values.get("item_no"), content)
                if key in seen:
                    index = seen[key]
                    previous = items[index]
                    _require(not (code is None and edited_jira and previous.jira_code) and
                             not ("jira_code" in previous.edited_fields and previous.jira_code is None and code),
                             "같은 Item의 Jira 삭제와 코드 입력이 상충합니다.")
                    try:
                        combined = join_codes((*split_codes(previous.jira_code), *split_codes(code)))
                    except ValueError as error:
                        raise StorageValidationError(str(error)) from error
                    items[index] = replace(previous,
                        jira_code=combined,
                        source_reference=merge_sources(previous.source_reference, item_sources),
                        edited_fields=tuple(dict.fromkeys((*previous.edited_fields, *_edited(row, ITEM_EDITED)))))
                    continue
                seen[key] = len(items)
                items.append(ItemInput(content=content, batch_id=batch_id, item_no=row.values.get("item_no"),
                                       jira_code=code, source_reference=item_sources,
                                       edited_fields=_edited(row, ITEM_EDITED)))
            result.append((release, tuple(items)))
        return result

    # --- 연결 ----------------------------------------------------------------------

    def _remember_workbooks(self, requests) -> None:
        """저장 행마다 통합문서 이름을 기억한다. 릴리즈노트는 사이트·라인 칸이 없어 파일명으로 파운드리를 가른다."""
        self._row_workbooks = {id(row): request.workbook_name for request in requests for row in request.rows}
        self._row_raw_data = {id(row): request.raw_data for request in requests for row in request.rows}

    def _model(self, tx, values: Mapping[str, Any], row: SaveRow) -> dict:
        raw = (values.get("model_name") or "").strip()
        name = MODEL_ALIASES.get(raw, raw)
        alias = resolve_model_text(raw, values.get("version_raw"))
        if alias is not None:
            name = alias[0]          # 번역표(멘토 확정 분류표)의 표기는 등록 모델명으로 푼다
        _require(bool(name), f"{_where(row)}: 모델명이 필요합니다.")
        category = _category(values.get("group_name"), name, row)
        matches = [g for g in self.repository.rows(tx, "equipment_group", category=category) if g["group_name"] == name]
        _require(len(matches) == 1, f"{_where(row)}: 모델 '{raw}'({category})에 대응하는 등록 모델이 없거나 여러 개입니다.")
        return matches[0]

    def _family(self, tx, model: dict, values: Mapping[str, Any], row: SaveRow, *, equipment: bool = False) -> dict:
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
        if not ui_raw and model["group_name"] == "M-C3" and equipment:
            # 멘토 확정(2026-10-28): UI 표기가 없는 M-C3는 통합UI. 저장 전 검증(normalization)과 같은 기본값.
            ui = "INTEGRATED"
        owners = {model["group_id"]} | ({model["parent_group_id"]} if model.get("parent_group_id") else set())
        families = [f for owner in sorted(owners) for f in self.repository.rows(tx, "version_family", group_id=owner)]
        # 정확한 계열명이 다른 UI의 후보를 가리켜도 올바른 UI의 접두 이름 후보를 버리지 않는다.
        if ui:
            families = [family for family in families if family["ui_type"] == ui]
        # 메모리/파운드리: 파운드리 라인의 O2·C3·C4는 ' (파운드리)' 계열(별도 릴리즈노트, 10/27).
        # 값이 없으면 사이트·라인·시트명으로 기본값을 정한다(장소표). 그래도 모르면 메모리 계열.
        area = area_of(values, _sheet_of(row), getattr(self, "_row_workbooks", {}).get(id(row)))
        base_model = model["group_name"] if not model.get("parent_group_id") else next(
            (g["group_name"] for g in self.repository.rows(tx, "equipment_group") if g["group_id"] == model["parent_group_id"]),
            model["group_name"])
        is_foundry = area == "FOUNDRY" and base_model in FOUNDRY_FAMILY_MODELS
        families = [f for f in families if strip_foundry(f["family_name"])[1] == is_foundry]
        plain = strip_foundry(name)[0]
        target = plain + FOUNDRY_SUFFIX if is_foundry else plain
        candidates = [f for f in families if f["family_name"] == target] or [
            f for f in families if f["family_name"].startswith(plain + " ")]
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

def batch_raw_data(raw: Mapping[str, Any], workbook_name: str | None = None) -> dict[str, Any]:
    """가져오기 원본(``{sheet, cells, merged}``)을 입력 작업 형식(``{시트: {셀: 값}}``)으로 바꾼다.

    병합 범위는 입력 작업 형식에 자리가 없어 넣지 않는다. 병합 셀의 값은 왼쪽 위 셀에 남아 있다.
    workbook_name을 주면 데이터 관리 변경 이력에 보이도록 파일 이름을 WORKBOOK_KEY로 함께 보관한다 (11-10 결정).
    """
    _require(isinstance(raw, Mapping) and isinstance(raw.get("sheet"), str) and isinstance(raw.get("cells"), Mapping),
             "원본 시트 자료가 올바르지 않습니다.")
    return with_workbook({raw["sheet"]: dict(raw["cells"])}, workbook_name)


def _refresh_context(requests):
    """GUI에서 미리 복사한 목표 조건을 저장 묶음 전체에 동일하게 적용한다.

    param
    - requests: 같은 저장 작업에 포함된 SaveRequest 목록.

    return
    - 요청의 RefreshContext. 조건이 없으면 목표를 추정하지 않음.
    """
    contexts = [request.refresh_context for request in requests if request.refresh_context is not None]
    _require(not contexts or all(context == contexts[0] for context in contexts), "같은 저장 묶음의 목표 N 조건이 다릅니다.")
    return contexts[0] if contexts else RefreshContext()


def _check_confirmations(requests):
    """사용자의 전체 노트·파일명 확인과 검토 결과의 형식을 검사한다.

    param
    - requests: 함께 저장하거나 미리 검토할 SaveRequest 목록.

    raises
    - StorageValidationError: 확인 값이 불리언이 아니거나 검토 결과가 튜플이 아닌 경우.
    """
    for request in requests:
        _require(type(request.release_note_confirmed) is bool and type(request.filename_confirmed) is bool,
                 "전체 누적 노트·파일명 확인 여부는 불리언이어야 합니다.")
        _require(type(request.release_reviews) is tuple, "계열별 검토 결과는 튜플이어야 합니다.")


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
            canonical = "jira_code" if name == "jira_key" else name
            result = merge_sources(result, {canonical: converted}) or {}
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
        sources = {}
        for name, locations in (_sources(row, tuple(row.values)) or {}).items():
            for location in locations if isinstance(locations, list) else (locations,):
                if location["sheet"] == request.raw_data.get("sheet") and location["cells"]:
                    sources[name] = location["cells"][0]
                    break
        rows.append(ReviewRow(index, {}, sources=sources, normalized=values,
                              edits={name: values.get(name) for name in row.edited_fields}))
    raw = request.raw_data
    positions = [parse_address(address) for address in raw.get("cells", {})]
    sheet = SheetSnapshot(raw.get("sheet", ""), 1, 1,
                          max((row for row, _ in positions), default=1),
                          max((column for _, column in positions), default=1), raw.get("cells", {}))
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


def _sheet_of(row: SaveRow) -> str | None:
    """행의 원본 출처에서 시트명을 하나 찾는다(메모리/파운드리 기본값 판정용). 없으면 None."""
    for value in (row.source_reference or {}).values():
        for location in value if isinstance(value, list) else [value]:
            if isinstance(location, dict) and location.get("sheet"):
                return str(location["sheet"])
    return None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StorageValidationError(message)
