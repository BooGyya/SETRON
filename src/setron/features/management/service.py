"""검토한 관리값 변경을 한 트랜잭션으로 저장하고 최신 판정을 반환한다."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select

from setron.features.aging.repository import thresholds_in
from setron.features.aging.schemas import SETTING_KEY
from setron.features.aging.service import equipment_age, parse_thresholds
from setron.features.equipment.read import read_equipment_records
from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.schemas import EquipmentSourceCell
from setron.features.export.schemas import StatusReport, TargetRow
from setron.features.item.schemas import ItemState, TargetScope, VersionItem
from setron.features.item.service import ItemCatalog
from setron.features.release.refresh import (ReleaseRefreshService, _context_copy, families_from_rows,
                                             fresh_context_in, rule_from_row)
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.schemas import ItemLinkInput
from setron.features.release.service import ReleaseService
from setron.features.release.validation import ITEM_EDIT_RECORDED, RELEASE_EDITABLE, prepare_links
from setron.features.risk.impact import DRAFT_RULE_ID, RuleImpact, preview_rule_impact
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.features.storage.inputs import identifier, require, required_text
from setron.features.storage.validation import StorageValidationError, decode_record
from setron.features.targets.groups import GroupStore, history_key_bounds
from setron.features.targets.service import TargetService, _key as target_key

from .schemas import ApplicabilityEdit, ApplicabilityView, ManagedRecord, ManagementResult, RecordEdit
from .validation import validate_changes_in, validate_risk_in, validate_snapshot_in


EDITABLE = {
    "equipment": frozenset({"equipment_code"}),
    "equipment_snapshot": frozenset({"line", "model_name", "group_id", "production_area",
                                     "structure_type", "geometry_type", "modification_type"}),
    "equipment_version": frozenset({"family_id", "version_raw", "release_version_id"}),
    "equipment_pm": frozenset({"group_id", "model_name_raw", "family_id", "version_raw", "release_version_id"}),
    "release_version": RELEASE_EDITABLE,
    "item": ITEM_EDIT_RECORDED,
    "release_version_item": frozenset({"item_state", "target_scope", "target_group_ids"}),
    "risk_rule": frozenset({"rule_type", "family_id", "group_id", "item_id", "release_version_id",
                            "start_release_version_id", "end_release_version_id", "risk_reason",
                            "is_enabled", "mapping_status"}),
    "app_setting": frozenset({"setting_value"}),
}
_SOFTWARE = frozenset({"family_id", "version_raw", "release_version_id"})


def _json_value(value):
    """지문·수정 근거에 사용하는 날짜를 손실 없는 ISO 문자열로 바꾼다."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"지원하지 않는 값 형식: {type(value).__name__}")


def _digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      default=_json_value, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class ManagementService:
    """관리 화면용 조회와 낙관적 충돌 검사·검증·묶음 저장 경계.

    site·base_date·고정 PM 위치와 가져오기 원본은 불변이다. 선택 N은 요청 조건으로
    유지한다. 결과는 commit 성공 뒤에만 반환하며 AI를 호출하거나 자동 재시도하지 않는다.
    DB 접속·저장·최종 결과 미확인 오류는 공통 Database의 구분을 그대로 전달한다.
    """

    def __init__(self, storage, context=RefreshContext()):
        self.storage = storage
        self.context = _context_copy(context)
        self.rows = EquipmentRepository()
        self.releases = ReleaseService(storage)

    def get_record(self, table, key):
        """기본키의 현재값·원본 셀·편집 가능 항목과 검토 지문을 조회한다."""
        self._table(table)
        with self.storage.transaction() as tx:
            return self._view(tx, table, tx.get(table, key))

    def list_records(self, table, *, limit=100, offset=0, filters=None):
        """관리 종류의 기록을 기본키 순으로 나누어 조회한다. 필터는 실제 컬럼의 동등 조건이다."""
        self._table(table)
        require(type(limit) is int and 1 <= limit <= 1000 and type(offset) is int and offset >= 0,
                "조회 개수는 1~1000, 시작 위치는 0 이상의 정수여야 합니다.")
        require(filters is None or isinstance(filters, dict), "조회 조건은 필드별 객체여야 합니다.")
        with self.storage.transaction() as tx:
            definition = tx.repository.table(table)
            require(set(filters or {}) <= set(definition.c.keys()), "지원하지 않는 조회 조건입니다.")
            # 원문 바이너리 컬럼도 UI에서 읽은 문자열 그대로 검색할 수 있게 한다.
            from setron.features.storage.validation import BINARY_COLUMNS
            conditions = {key: (value.encode("utf-8") if key in BINARY_COLUMNS.get(table, {})
                                and isinstance(value, str) else value) for key, value in (filters or {}).items()}
            query = select(definition).filter_by(**conditions).order_by(*definition.primary_key.columns)
            records = tx.session.execute(query.limit(limit).offset(offset)).mappings().all()
            return tuple(self._view(tx, table, decode_record(table, dict(row))) for row in records)

    def get_applicability(self, model_id, family_id):
        """적용 가능 버전의 기존 확인 근거와 전용 수정 지문을 조회한다."""
        identifier(model_id, "model_id")
        identifier(family_id, "family_id")
        with self.storage.transaction() as tx:
            value = TargetService(None).read_in(tx, ((model_id, family_id),)).pairs[0]
            return ApplicabilityView(value, self._applicability_revision(tx, model_id, family_id))

    def save(self, edits=(), *, applicability=()):
        """오류·충돌이 없는 수정 묶음만 저장한다. 실패하면 변경과 재판정을 모두 취소한다.

        먼저 모든 검토 지문을 검사하고 나서 수정하므로 한 묶음 안의 변경이 서로를
        오래된 요청으로 판정하지 않는다. 원본·수정 전 관리값은 별도 MANUAL 입력
        작업에 보존하며, 기존 입력 작업과 source_reference를 덮어쓰지 않는다.
        """
        edits, applicability = deepcopy(edits), deepcopy(applicability)
        self._requests(edits, applicability)
        context = _context_copy(self.context)
        with self.storage.transaction() as tx:
            self._lock(tx)
            before = []
            identities = set()
            canonical = []
            for edit in edits:
                view = self._view(tx, edit.table, tx.get(edit.table, edit.key))
                identity = (edit.table, _digest(view.key))
                require(identity not in identities, "같은 기록의 수정 요청이 중복됐습니다.")
                identities.add(identity)
                require(view.revision == edit.revision,
                        f"{edit.table}: 검토 후 자료가 바뀌었습니다. 다시 조회·검토해 주세요.")
                require(set(edit.changes) <= set(view.editable_fields), "원본·식별자 또는 편집할 수 없는 항목입니다.")
                validate_changes_in(tx, edit.table, view.current, edit.changes)
                before.append(self._before(tx, edit.table, view.current))
                canonical.append(RecordEdit(edit.table, view.key, edit.revision, edit.changes))
            edits = tuple(canonical)
            for change in applicability:
                require(change.revision == self._applicability_revision(tx, change.model_id, change.family_id),
                        "모델 적용 근거가 바뀌었습니다. 다시 조회·검토해 주세요.")
            pm_changes = self._plan_pm_changes(tx, edits)
            self._unique_names(tx, edits)
            snapshots, invalidated, pm_snapshots, changed_applicability = set(), set(), set(), set()
            explicit_groups = {e.key["snapshot_id"] for e in edits
                               if e.table == "equipment_snapshot" and "group_id" in e.changes}
            for edit in edits:
                row = tx.get(edit.table, edit.key)
                if "snapshot_id" in row:
                    snapshots.add(row["snapshot_id"])
                if edit.table == "equipment":
                    snapshots.update(r["snapshot_id"] for r in self.rows.rows(tx, "equipment_snapshot",
                                     equipment_id=row["equipment_id"]))
                if edit.table in {"release_version", "item"}:
                    updated = self.releases._edit(tx, edit.table, edit.key, edit.changes)
                    if edit.table == "release_version" and updated != row:
                        changed_applicability.add(row["release_version_id"])
                    if edit.table == "release_version" and edit.changes.get("version_raw", row["version_raw"]) != row["version_raw"]:
                        invalidated.add(row["release_version_id"])
                elif edit.table == "equipment_pm":
                    self._edit_pm(tx, row, pm_changes[row["pm_id"]])
                    pm_snapshots.add(row["snapshot_id"])
                elif edit.table == "equipment_version":
                    require(not self.rows.rows(tx, "equipment_pm", equipment_version_id=row["equipment_version_id"]),
                            "PM이 공유하는 SW는 수정할 PM 위치를 선택해 수정해야 합니다.")
                    tx.update(edit.table, edit.key, self._software_changes(row, edit.changes))
                elif edit.table == "release_version_item":
                    self._edit_link(tx, row, edit.changes)
                elif edit.table == "risk_rule":
                    self._edit_risk(tx, row, edit.changes)
                elif edit.table == "app_setting":
                    require(row["setting_key"] == SETTING_KEY, "적용 후보는 전용 근거 수정 요청으로 저장해야 합니다.")
                    value = parse_thresholds(edit.changes.get("setting_value", row["setting_value"]))
                    tx.update(edit.table, edit.key, {"setting_value": value.to_setting()})
                else:
                    tx.update(edit.table, edit.key, edit.changes)
            for snapshot_id in sorted(pm_snapshots - explicit_groups):
                models = {p["group_id"] for p in self.rows.rows(tx, "equipment_pm", snapshot_id=snapshot_id)}
                tx.update("equipment_snapshot", {"snapshot_id": snapshot_id},
                          {"group_id": next(iter(models)) if len(models) == 1 else None})
            for change in applicability:
                TargetService(None).confirm_in(tx, change.model_id, change.family_id, change.candidates)
            # 변경 전 명시 목록을 먼저 무효화한 뒤, 같은 묶음에서 새로 확인한 근거만 복구한다.
            context, _ = fresh_context_in(tx, context,
                invalidated_applicability_release_ids=tuple(sorted(changed_applicability)))
            context = self._current_applicability(tx, context, applicability)
            # 날짜별 분류도 이번 묶음의 모든 명시적 수정을 적용한 최종 현황으로 검사한다.
            for snapshot_id in sorted(snapshots):
                validate_snapshot_in(tx, snapshot_id)
            for edit in edits:
                if edit.table == "risk_rule":
                    validate_risk_in(tx, tx.get(edit.table, edit.key), context)
            # 잘못 제출된 연결을 자동 재연결로 숨기지 않도록 입력 검증 후 재판정한다.
            refresh = ReleaseRefreshService().refresh_in(tx, context=context,
                                                        invalidated_release_ids=tuple(sorted(invalidated)))
            thresholds = self._thresholds(tx)
            ages = tuple((d.base_date, tuple(equipment_age(e.record, refresh.families, thresholds)
                                            for e in d.summary.equipment)) for d in refresh.dates)
            journal = {"schema": 1, "management_edit": {
                "before": before, "changes": [asdict(e) for e in edits],
                "applicability": [asdict(e) for e in applicability],
            }}
            # 날짜를 JSON으로 변환하고 SW 행 통합 후에도 원래 셀 위치를 조회할 수 있게 한다.
            journal = json.loads(json.dumps(journal, ensure_ascii=False, default=_json_value, allow_nan=False))
            audit = tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": journal})
            records = tuple(self._view(tx, e.table, tx.get(e.table, e.key)) for e in edits)
            result = ManagementResult(records, refresh, ages, thresholds, audit["batch_id"])
        return result

    def create_risk_rule(self, values):
        """환경안전·결함·위험 버전 지정을 새로 등록하고 같은 트랜잭션에서 재판정한다 (BE-015).

        ``values``: rule_type, family_id, risk_reason과 유형별 연결(item_id·release_version_id·
        start/end_release_version_id), 선택 group_id, mapping_status(기본 UNRESOLVED), is_enabled(기본 True).
        재연결 근거(target_reference)는 등록 시점의 계열·Item·버전 이름으로 만든다. 연결이 맞지 않으면 저장하지 않는다.
        """
        require(isinstance(values, Mapping), "등록할 위험 정보가 필요합니다.")
        values = deepcopy(dict(values))
        presentation = values.pop("item_manager", None)
        allowed = {"rule_type", "family_id", "group_id", "item_id", "release_version_id", "start_release_version_id",
                   "end_release_version_id", "risk_reason", "is_enabled", "mapping_status"}
        require(set(values) <= allowed, "위험 등록에 쓸 수 없는 항목이 있습니다.")
        row = {"is_enabled": True, "mapping_status": "UNRESOLVED", **deepcopy(dict(values))}
        context = _context_copy(self.context)
        with self.storage.transaction() as tx:
            self._lock(tx)
            row["target_reference"] = self._risk_reference(tx, row)
            if presentation is not None:
                row["target_reference"]["item_manager"] = presentation
            # 같은 검사를 등록 전에 거쳐 잘못된 연결을 DB에 남기지 않는다 (ID는 임시값).
            validate_risk_in(tx, {**row, "risk_rule_id": 1}, context)
            created = tx.insert("risk_rule", row)
            validate_risk_in(tx, created, context)
            refresh = ReleaseRefreshService().refresh_in(tx, context=context)
            thresholds = self._thresholds(tx)
            ages = tuple((d.base_date, tuple(equipment_age(e.record, refresh.families, thresholds)
                                            for e in d.summary.equipment)) for d in refresh.dates)
            journal = json.loads(json.dumps({"schema": 1, "management_create": {"table": "risk_rule", "values": created}},
                                            ensure_ascii=False, default=_json_value, allow_nan=False))
            audit = tx.insert("import_batch", {"source_type": "MANUAL", "raw_data": journal})
            record = self._view(tx, "risk_rule", tx.get("risk_rule", {"risk_rule_id": created["risk_rule_id"]}))
            return ManagementResult((record,), refresh, ages, thresholds, audit["batch_id"])

    def preview_risk_rule(self, values) -> RuleImpact:
        """위험 정보를 저장하지 않고, 등록했을 때 최신 관측일 설비와 릴리즈에 주는 영향을 계산한다 (BE-015).

        ``values``는 :meth:`create_risk_rule`과 같다. 기본값도 같아서(mapping_status UNRESOLVED 등)
        저장했을 때와 같은 판정을 미리 보여 준다. 연결·범위 검사에서 걸리면 ``problems``에 사유를 담고
        영향은 계산하지 않는다. 읽기만 하며 DB·판정 결과를 바꾸지 않는다.
        """
        require(isinstance(values, Mapping), "미리 볼 위험 정보가 필요합니다.")
        values = deepcopy(dict(values))
        presentation = values.pop("item_manager", None)
        require(set(values) <= EDITABLE["risk_rule"], "위험 등록에 쓸 수 없는 항목이 있습니다.")
        row = {"is_enabled": True, "mapping_status": "UNRESOLVED", "group_id": None, "item_id": None,
               "release_version_id": None, "start_release_version_id": None, "end_release_version_id": None,
               **deepcopy(dict(values)), "risk_rule_id": DRAFT_RULE_ID}
        context = _context_copy(self.context)
        with self.storage.transaction() as tx:
            problems = ()
            try:
                reference = self._risk_reference(tx, row)  # 등록과 같은 검사를 거치도록 같은 근거를 만든다
                if presentation is not None:
                    reference["item_manager"] = presentation
                row["target_reference"] = reference
                validate_risk_in(tx, {**row, "target_reference": reference, "risk_rule_id": 1}, context)
            except (StorageValidationError, LookupError, ValueError) as error:
                problems = (str(error),)
            repository = EquipmentRepository()
            families = families_from_rows(repository.rows(tx, "version_family"),
                                          repository.rows(tx, "release_version"), context.aliases)
            links = repository.rows(tx, "release_version_item")
            catalog = ItemCatalog(families, tuple(VersionItem(
                link["release_version_id"], link["item_id"], ItemState(link["item_state"]),
                TargetScope(link["target_scope"]), tuple(link["target_group_ids"] or ()),
            ) for link in links))
            rules = tuple(rule_from_row(rule, families, links) for rule in repository.rows(tx, "risk_rule"))
            excluded = {g["group_id"] for g in repository.rows(tx, "equipment_group") if not g["analysis_enabled"]}
            snapshot_rows = repository.rows(tx, "equipment_snapshot")
            base_date = max((s["base_date"] for s in snapshot_rows), default=None)
            equipment = {e["equipment_id"]: e for e in repository.rows(tx, "equipment")}
            records = read_equipment_records(tuple(
                repository.load(tx, equipment[s["equipment_id"]], s) for s in snapshot_rows
                if s["base_date"] == base_date), excluded)
        try:
            draft = rule_from_row(row, families, links)
        except (KeyError, ValueError, TypeError):
            require(bool(problems), "위험 정보의 유형이나 계열을 확인해 주세요.")
            draft = RiskRule(DRAFT_RULE_ID, RuleType.DIRECT_VERSION, 0, str(row.get("risk_reason") or ""),
                             mapping_status=MappingStatus.UNRESOLVED)
        return preview_rule_impact(draft, families, catalog, rules, records, base_date=base_date, problems=problems)

    @staticmethod
    def _risk_reference(tx, row):
        """릴리즈 교체 뒤 재연결에 쓰는 근거. 릴리즈 교체(replacement)가 읽는 키 이름을 그대로 쓴다."""
        family = tx.get("version_family", {"family_id": row.get("family_id")})
        reference = {"family": {"family_id": family["family_id"], "family_name": family["family_name"]}}
        for field, key in (("release_version_id", "direct_version"), ("start_release_version_id", "start_version"),
                           ("end_release_version_id", "end_version")):
            if row.get(field) is not None:
                release = tx.get("release_version", {"release_version_id": row[field]})
                reference[key] = {name: release[name] for name in
                                  ("release_version_id", "family_id", "version_raw", "version_suffix")}
        if row.get("item_id") is not None:
            item = tx.get("item", {"item_id": row["item_id"]})
            reference["item"] = {"item_id": item["item_id"], "item_no": item.get("item_no"),
                                 "content": item.get("content"), "family_id": family["family_id"]}
        return reference

    def status_report(self, result, base_date, *, scope=(), filters=None):
        """실제 조회 범위를 적용한 집계·위험·노후도를 기존 XLSX 출력에 전달한다."""
        from .report import build_management_status_report

        with self.storage.transaction() as tx:
            groups = {r["group_id"]: r["group_name"] for r in self.rows.rows(tx, "equipment_group")}
            families = {r["family_id"]: r["family_name"] for r in self.rows.rows(tx, "version_family")}
        return build_management_status_report(result, base_date, group_names=groups, family_names=families,
                                              scope=scope, filters=filters)

    def current_report(self, base_date, *, scope=(), filters=None):
        """요청 시 저장된 최신 관측·판정·기간 설정을 같은 트랜잭션에서 읽어 출력한다.

        AI와 수정 이력은 생성하지 않는다. 릴리즈 대응의 재판정만 기존 refresh 경로로 수행한다.
        """
        from .report import build_status_report_from_refresh

        with self.storage.transaction() as tx:
            self._lock(tx)
            context, _ = fresh_context_in(tx, _context_copy(self.context))
            refresh = ReleaseRefreshService().refresh_in(tx, context=context)
            thresholds = self._thresholds(tx)
            groups = {r["group_id"]: r["group_name"] for r in self.rows.rows(tx, "equipment_group")}
            families = {r["family_id"]: r["family_name"] for r in self.rows.rows(tx, "version_family")}
            # 화면(대시보드·히트맵)과 같은 기준: 저장된 N 그룹이 있으면 그 모델·계열은 그룹으로 판정한다 (QA Q-10).
            store = GroupStore(self.storage)
            saved = {}
            for target in refresh.targets:
                pair = (target.model_id, target.family_id)
                stored = store.load_current_in(tx, pair)
                if stored is not None:
                    saved[pair] = stored.groups
            return build_status_report_from_refresh(refresh, thresholds, base_date,
                group_names=groups, family_names=families, scope=scope, filters=filters, saved_groups=saved)

    @staticmethod
    def _table(table):
        require(isinstance(table, str) and table in EDITABLE, "지원하지 않는 관리 데이터 종류입니다.")

    def _requests(self, edits, applicability):
        require(isinstance(edits, tuple) and isinstance(applicability, tuple) and bool(edits or applicability),
                "저장할 수정 요청 튜플이 필요합니다.")
        identities = []
        for edit in edits:
            require(isinstance(edit, RecordEdit), "RecordEdit 형식이 필요합니다.")
            self._table(edit.table)
            require(isinstance(edit.key, dict) and bool(edit.key), "수정할 기본키가 필요합니다.")
            required_text(edit.revision, "revision", 64)
            require(isinstance(edit.changes, dict) and bool(edit.changes)
                    and set(edit.changes) <= EDITABLE[edit.table], "수정 항목이 없거나 편집할 수 없는 항목입니다.")
            identities.append((edit.table, _digest(edit.key)))
        require(len(set(identities)) == len(identities), "같은 기록의 수정 요청이 중복됐습니다.")
        pairs = []
        for change in applicability:
            require(isinstance(change, ApplicabilityEdit), "ApplicabilityEdit 형식이 필요합니다.")
            identifier(change.model_id, "model_id")
            identifier(change.family_id, "family_id")
            required_text(change.revision, "revision", 64)
            require(isinstance(change.candidates, tuple), "모델 적용 근거는 튜플이어야 합니다.")
            pairs.append((change.model_id, change.family_id))
        require(len(set(pairs)) == len(pairs), "모델·계열 적용 근거 수정이 중복됐습니다.")

    def _lock(self, tx):
        """릴리즈 수정과 같은 계열→릴리즈 순서로 잠근 뒤 현재 관리 자료를 고정한다."""
        for name in ("version_family", "release_version", "equipment_group", "item", "risk_rule",
                     "release_version_item", "equipment", "equipment_snapshot", "equipment_version",
                     "equipment_pm"):
            self.rows.rows(tx, name, lock=True)
        # 이전 N 기준 이력은 추가만 하는 기록이라 관리값 저장과 보고서 조회에서 잠글 필요가 없다.
        setting_key = tx.repository.table("app_setting").c.setting_key
        first, last = history_key_bounds()
        tx.session.execute(select(setting_key).where((setting_key < first) | (setting_key >= last))
                           .with_for_update()).all()

    def _view(self, tx, table, row):
        definition = tx.repository.table(table)
        key = {field: row[field] for field in definition.primary_key.columns.keys()}
        current, evidence = deepcopy(row), [row]
        if table in {"equipment_snapshot", "equipment_version", "equipment_pm"}:
            sid = row["snapshot_id"]
            evidence += self.rows.rows(tx, "equipment_snapshot", snapshot_id=sid)
            evidence += self.rows.rows(tx, "equipment_version", snapshot_id=sid)
            evidence += self.rows.rows(tx, "equipment_pm", snapshot_id=sid)
        if table == "equipment_pm":
            software = tx.get("equipment_version", {"equipment_version_id": row["equipment_version_id"]})
            current.update({field: software[field] for field in _SOFTWARE})
        if table == "equipment":
            evidence += self.rows.rows(tx, "equipment_snapshot", equipment_id=row["equipment_id"])
        if table in {"release_version", "item"}:
            evidence += self.rows.rows(tx, "release_version_item", **key)
        editable = EDITABLE[table]
        if table == "app_setting" and row["setting_key"] != SETTING_KEY:
            editable = frozenset()
        if table == "equipment_version" and self.rows.rows(tx, "equipment_pm", equipment_version_id=row["equipment_version_id"]):
            editable = frozenset()
        sources = list(self._sources(tx, row))
        if table == "equipment":
            for snapshot in self.rows.rows(tx, "equipment_snapshot", equipment_id=row["equipment_id"]):
                sources += [cell for cell in self._sources(tx, snapshot) if cell.field == "equipment_code"]
        if table == "equipment_pm":
            sources += self._sources(tx, software)
            # 통합된 이전 SW의 원본 참조도 수정 전 보존 자료에서 표시한다.
            for batch in self.rows.rows(tx, "import_batch", source_type="MANUAL"):
                journal = (batch["raw_data"] or {}).get("management_edit", {})
                for previous in journal.get("before", []):
                    if previous.get("table") == table and previous.get("current", {}).get("pm_id") == row["pm_id"]:
                        sources += self._sources(tx, previous.get("software", {}))
        unique = {}
        for cell in sources:
            unique[(cell.field, cell.batch_id, cell.sheet, cell.address)] = cell
        return ManagedRecord(table, key, current, tuple(sorted(editable)), tuple(unique.values()), _digest(evidence))

    @staticmethod
    def _sources(tx, row):
        cells = []
        for field, locations in (row.get("source_reference") or {}).items():
            for location in locations if isinstance(locations, list) else [locations]:
                bid = location.get("batch_id", row.get("batch_id"))
                batch = tx.get("import_batch", {"batch_id": bid})
                raw = (batch["raw_data"] or {}).get(location["sheet"], {})
                for address in location["cells"]:
                    available = isinstance(raw, dict) and address in raw
                    cells.append(EquipmentSourceCell(field=field, batch_id=bid, sheet=location["sheet"],
                                 address=address, value=deepcopy(raw[address]) if available else None, available=available))
        return cells

    @staticmethod
    def _before(tx, table, row):
        record = {"table": table, "current": deepcopy(row)}
        if table == "equipment_pm":
            record["software"] = tx.get("equipment_version", {"equipment_version_id": row["equipment_version_id"]})
        return record

    def _applicability_revision(self, tx, model_id, family_id):
        key = {"setting_key": target_key((model_id, family_id))}
        try:
            setting = tx.get("app_setting", key)
        except LookupError:
            setting = None
        return _digest([setting, self.rows.rows(tx, "version_family", family_id=family_id),
                        self.rows.rows(tx, "release_version", family_id=family_id),
                        self.rows.rows(tx, "equipment_group")])

    @staticmethod
    def _current_applicability(tx, context, changes):
        """저장된 근거가 있는 쌍은 매번 재검증해 이전 호출의 대응을 재사용하지 않는다."""
        requested = set(context.targets) | {(change.model_id, change.family_id) for change in changes}
        pairs = []
        for pair in sorted(requested):
            try:
                tx.get("app_setting", {"setting_key": target_key(pair)})
            except LookupError:
                continue
            pairs.append(pair)
        if not pairs:
            return context
        confirmed = TargetService(None).read_in(tx, tuple(pairs), context.targets)
        current = {pair: values for pair, values in context.applicability.items() if pair not in pairs}
        current.update(confirmed.applicability)
        return replace(context, applicability=current)

    @staticmethod
    def _software_changes(row, changes):
        result = deepcopy(changes)
        if any(field in changes and changes[field] != row[field] for field in ("family_id", "version_raw")):
            result.setdefault("release_version_id", None)
        return result

    def _edit_pm(self, tx, pm, changes):
        old = tx.get("equipment_version", {"equipment_version_id": pm["equipment_version_id"]})
        sw_changes = self._software_changes(old, {k: v for k, v in changes.items() if k in _SOFTWARE})
        pm_changes = {k: v for k, v in changes.items() if k not in _SOFTWARE}
        if sw_changes:
            combined = {**old, **sw_changes}
            siblings = self.rows.rows(tx, "equipment_pm", equipment_version_id=old["equipment_version_id"])
            targets = [row for row in self.rows.rows(tx, "equipment_version", snapshot_id=pm["snapshot_id"])
                       if row["family_id"] == combined["family_id"] and row["version_raw"] == combined["version_raw"]]
            target = targets[0] if targets else None
            if target and target["equipment_version_id"] != old["equipment_version_id"]:
                require("release_version_id" not in changes
                        or target["release_version_id"] == combined["release_version_id"],
                        "기존 SW를 공유하는 다른 PM의 릴리즈 연결과 다릅니다. 연결을 함께 검토해 주세요.")
            elif len(siblings) == 1 or all(old[k] == combined[k] for k in _SOFTWARE):
                target = tx.update("equipment_version", {"equipment_version_id": old["equipment_version_id"]}, sw_changes)
            else:
                require(target is None, "공유 SW의 릴리즈 연결을 바꾸려면 다른 PM의 확인 결과도 함께 검토해야 합니다.")
                target = tx.insert("equipment_version", {k: v for k, v in combined.items() if k != "equipment_version_id"})
            pm_changes["equipment_version_id"] = target["equipment_version_id"]
        tx.update("equipment_pm", {"pm_id": pm["pm_id"]}, pm_changes)
        if sw_changes and target["equipment_version_id"] != old["equipment_version_id"] and len(siblings) == 1:
            tx.repository.delete(tx.session, "equipment_version", {"equipment_version_id": old["equipment_version_id"]})

    def _edit_link(self, tx, row, changes):
        combined = {**row, **changes}
        try:
            link = ItemLinkInput(item_id=row["item_id"], item_state=ItemState(combined["item_state"]),
                                 target_scope=TargetScope(combined["target_scope"]),
                                 target_group_ids=tuple(combined["target_group_ids"] or ()))
        except (ValueError, TypeError):
            raise StorageValidationError("Item 포함 상태·모델 범위를 확인해야 합니다.") from None
        prepare_links((link,))
        release = tx.get("release_version", {"release_version_id": row["release_version_id"]})
        self.releases._link(tx, release, link)

    def _plan_pm_changes(self, tx, edits):
        """최종 SW 공유 구성을 먼저 검사해 수정 순서와 무관한 릴리즈 연결을 정한다.

        같은 SW에 남는 PM의 기존 연결과 명시한 연결은 모두 일치해야 한다. 다른
        SW로 이동하면서 연결을 생략한 PM만 최종 공유 SW의 확인된 연결을 따른다.
        """
        selected = {e.key["pm_id"]: e.changes for e in edits if e.table == "equipment_pm"}
        snapshots = {tx.get("equipment_pm", {"pm_id": pm_id})["snapshot_id"] for pm_id in selected}
        planned, link_updates = {}, []
        for snapshot_id in sorted(snapshots):
            software = self.rows.rows(tx, "equipment_version", snapshot_id=snapshot_id)
            by_id = {row["equipment_version_id"]: row for row in software}
            by_identity = {(row["family_id"], row["version_raw"]): row for row in software}
            pms = self.rows.rows(tx, "equipment_pm", snapshot_id=snapshot_id)
            referenced = {pm["equipment_version_id"] for pm in pms}
            groups = {}
            for pm in pms:
                old = by_id[pm["equipment_version_id"]]
                changes = selected.get(pm["pm_id"], {})
                final = {**old, **self._software_changes(old, {k: v for k, v in changes.items() if k in _SOFTWARE})}
                identity = (final["family_id"], final["version_raw"])
                # 비선택 PM과 원래 SW에 남는 PM의 연결을 조용히 덮어쓰지 않는다.
                fixed = "release_version_id" in changes or identity == (old["family_id"], old["version_raw"])
                groups.setdefault(identity, []).append((pm["pm_id"], final, fixed))
            for identity, members in groups.items():
                links = {final["release_version_id"] for _, final, fixed in members if fixed}
                existing = by_identity.get(identity)
                # 기존 비-PM SW도 선택하지 않은 관리값이다. 비정상 혼재 자료를
                # 수정하는 경우에도 이 행의 명시적 연결을 덮어쓰지 않는다.
                if existing and existing["equipment_version_id"] not in referenced:
                    links.add(existing["release_version_id"])
                require(len(links) <= 1, "최종 SW를 공유하는 PM의 릴리즈 연결이 다릅니다. 연결을 함께 검토해 주세요.")
                release_id = next(iter(links)) if links else existing["release_version_id"] if existing else None
                for pm_id, final, _ in members:
                    if pm_id in selected:
                        planned[pm_id] = {**selected[pm_id], "family_id": final["family_id"],
                                          "version_raw": final["version_raw"], "release_version_id": release_id}
                if existing and existing["release_version_id"] != release_id:
                    link_updates.append((existing["equipment_version_id"], release_id))
        # 모든 최종 그룹의 충돌 검사를 마친 뒤 기존 SW의 연결만 준비한다.
        for software_id, release_id in link_updates:
            tx.update("equipment_version", {"equipment_version_id": software_id}, {"release_version_id": release_id})
        return planned

    def _unique_names(self, tx, edits):
        """최종 원문 중복을 검사하고 같은 묶음의 이름 교환을 고유키 충돌 없이 준비한다."""
        for table, field in (("equipment", "equipment_code"), ("release_version", "version_raw")):
            selected = [e for e in edits if e.table == table and field in e.changes]
            if not selected:
                continue
            rows = self.rows.rows(tx, table)
            key_field = "equipment_id" if table == "equipment" else "release_version_id"
            updates = {e.key[key_field]: e.changes[field] for e in selected}
            final = [(r.get("family_id"), updates.get(r[key_field], r[field])) for r in rows]
            require(len(final) == len(set(final)), f"{table}.{field}: 수정 후 원문이 중복됩니다.")
            occupied = {r[field] for r in rows} | set(updates.values())
            for row in rows:
                identifier_value = row[key_field]
                if identifier_value not in updates or updates[identifier_value] == row[field]:
                    continue
                placeholder = f"__edit_{uuid4().hex}__"
                while placeholder in occupied:
                    placeholder = f"__edit_{uuid4().hex}__"
                occupied.add(placeholder)
                tx.update(table, {key_field: identifier_value}, {field: placeholder})

    @staticmethod
    def _edit_risk(tx, row, changes):
        """직접 바꾼 위험 연결의 활성 복원 근거만 갱신한다. 이전 근거는 수정 이력에 보존한다."""
        reference = deepcopy(row["target_reference"])
        fields = {"item_id": "item", "release_version_id": "direct_version",
                  "start_release_version_id": "start_version", "end_release_version_id": "end_version"}
        scope_changed = any(field in changes and changes[field] != row[field]
                            for field in ("family_id", "rule_type"))
        for field, evidence in fields.items():
            # None을 명시해 해제한 경계를 다음 릴리즈 교체가 다시 복원하지 않게 한다.
            if scope_changed or field in changes:
                reference.pop(evidence, None)
        tx.update("risk_rule", {"risk_rule_id": row["risk_rule_id"]},
                  {**changes, "target_reference": reference})

    @staticmethod
    def _thresholds(tx):
        return thresholds_in(tx).thresholds
