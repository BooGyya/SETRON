"""DB에 저장된 설비 현황·릴리즈로 대시보드 공급자를 만든다 (Jira S15P31B106-75).

대시보드·HeatMap·버전·Item 화면이 같은 :class:`DashboardSource`를 쓰므로, 이 공급자 하나로 조회 화면 전체가
DB 자료를 본다. '조회 적용'을 누를 때마다 DB를 다시 읽어 가져오기로 저장한 자료가 바로 반영된다.

정한 규칙
- 조회 선택지(Site·설비군)는 VER3 선택지에 DB 자료를 더해 만든다. Site는 이름이 같을 때만, 설비군은
  :data:`GROUP_MODELS`의 등록 모델 이름(개조형은 상위 설비군)으로 연결한다. 데이터 관리에서 등록한 새 설비군·Site는
  선택지로 덧붙인다 (11-11). 연결되지 않는 현황은 빼고 건수를 안내한다.
- HPA는 모델의 analysis_enabled = false로만 판단한다 (:func:`read_equipment_records`).
- 목표 N은 조회 조건으로 유지하고, 모델별 적용 근거는 DB의 현재 릴리즈로 다시 확인한다.
  저장된 위험 지정을 현재 관측에 적용하며 기준이 없으면 미확인으로 표시한다.
- 원본 구역(raw_area)은 기준일의 Line 원문을 쓴다. Fab/Line 선택지로는 추정하지 않는다.
"""

from __future__ import annotations

import inspect

from collections.abc import Callable
from dataclasses import replace
from threading import RLock

from setron.features.data_admin.service import registered_sites
from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.read import read_equipment_record
from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.features.item.service import ItemCatalog
from setron.features.aging.repository import thresholds_in
from setron.features.release.refresh import families_from_rows, rule_from_row
from setron.features.risk.schemas import RiskStatus
from setron.features.risk.service import RiskAssessor
from setron.features.targets.groups import GroupStore
from setron.features.targets.service import TargetService
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import DatabaseError
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding, RiskRuleState, family_name, model_name
from setron.ui.query_state import FabOption, GroupOption, LineOption, QueryCatalog, SiteOption, TargetN, preview_catalog
from setron.ui.target_data import target_pairs
from setron.ui.version_data import ItemEntry, ModelFamily, ReleaseEntry, SourceReference, VersionCatalog

# UI 설비군 키 → 등록 모델 이름. 시연용 기준 자료(database/seed_demo_catalog.sql)의 이름과 같다.
GROUP_MODELS = {
    "c3": ("M-C3",), "c4": ("M-C4", "M-C4 DFB", "M-C4+"), "o2": ("M-O2",), "o3": ("M-O3", "XMO3"), "o5": ("M-O5", "M-O5 Prime"),
    "inline-ox": ("Inline Ox",), "inline-o2": ("Inline O2",), "cluster-ox": ("Cluster Ox",), "cluster-o2": ("Cluster O2",),
}
SOURCE_LABEL = "MySQL DB 저장 자료 · 조회 적용 시 목표 N·위험 기준 재확인"


def database_catalog(snapshots, model_names=None, *, extra_groups=(), extra_sites=()) -> QueryCatalog:
    """저장된 현황의 실제 Site·Line으로 조회 선택지를 만든다.

    - Site: VER3 선택지(평택·화성·기흥, 키 유지) 뒤에 DB에만 있는 Site와 데이터 관리에서 등록한 Site(``extra_sites``)를
      이름 그대로 덧붙인다. 등록만 하고 관측이 없는 Site도 선택지에 보인다 (11-11).
    - Fab: 저장된 production_area의 Memory/Foundry이며 Site 공통 선택지다.
    - Line: 관측된 (Site, Fab, Line) 조합. 생산 영역이 없는 기존 키는 유지한다.
    - 설비군: VER3 선택지 중 :data:`GROUP_MODELS`의 모델이 DB에 등록된 것만 둔다 (``model_names``).
      등록 모델이 없는 설비군(예: Inline Ox·Cluster Ox)은 골라도 항상 0대라 빼고, ``model_names``가 없으면 모두 둔다.
      데이터 관리에서 등록한 새 기본형 설비군(``extra_groups``, :func:`registered_group_options`)은 같은 분류 뒤에 넣는다.
    """
    snapshots = tuple(snapshots)
    base = preview_catalog()
    known = {option.label for option in base.sites}
    extra = sorted({value.site for value in snapshots if value.site and value.site not in known}
                   | {name for name in extra_sites if name and name not in known})
    sites = (*base.sites, *(SiteOption(name, name) for name in extra))
    keys = {option.label: option.key for option in sites}
    lines = sorted({(keys[value.site], value.production_area, value.line) for value in snapshots
                    if value.site in keys and value.line}, key=lambda value: (value[0], value[1] or "", value[2]))
    groups = [option for option in base.groups if model_names is None
              or any(name in model_names for name in GROUP_MODELS.get(option.key, ()))]
    for option in extra_groups:
        same = [i for i, known_option in enumerate(groups) if known_option.category == option.category]
        groups.insert(same[-1] + 1 if same else len(groups), option)
    return QueryCatalog(
        sites=sites,
        fabs=(FabOption("MEMORY", "Memory", None), FabOption("FOUNDRY", "Foundry", None)),
        lines=tuple(LineOption(_line_key(site, fab, line), line, site, fab) for site, fab, line in lines),
        groups=tuple(groups),
        source_label="DB 저장 현황의 Site·Fab(Memory/Foundry)·Line",
    )


def group_keys(groups) -> dict:
    """DB 설비군 ID → 조회 설비군 키.

    :data:`GROUP_MODELS`에 이름이 있으면 그 키, 없으면 상위 설비군(개조형의 기본형)의 키를 따른다.
    둘 다 없으면 데이터 관리에서 새로 등록한 기본형이므로 ``group-<ID>``로 따로 둔다 (11-11).
    """
    named = {name: key for key, names in GROUP_MODELS.items() for name in names}
    by_id = {row["group_id"]: row for row in groups}

    def key_of(row, seen):
        if row["group_name"] in named:
            return named[row["group_name"]]
        parent = by_id.get(row.get("parent_group_id"))
        if parent is not None and parent["group_id"] not in seen:
            return key_of(parent, seen | {row["group_id"]})
        return f"group-{row['group_id']}"

    return {row["group_id"]: key_of(row, frozenset()) for row in groups}


def registered_group_options(groups, keys) -> tuple[GroupOption, ...]:
    """:data:`GROUP_MODELS`로 연결되지 않는 분석 대상 기본형 설비군(POLY·OXIDE·KCTC)의 조회 선택지."""
    return tuple(GroupOption(keys[row["group_id"]], row["group_name"], row["category"]) for row in sorted(
        groups, key=lambda row: row["group_id"])
        if keys[row["group_id"]] == f"group-{row['group_id']}" and row["category"] in ("POLY", "OXIDE", "KCTC")
        and row["analysis_enabled"])


def _line_key(site, fab, line):
    return f"{site}:{fab}:{line}" if fab is not None else f"{site}:{line}"


def load_dashboard_source(storage, catalog: QueryCatalog | None = None) -> DashboardSource:
    """DB를 한 번 읽어 대시보드 공급자를 만든다. 연결할 수 없는 현황은 빼고 ``source_label``에 건수를 적는다.

    ``catalog``를 주면 그 선택지를 그대로 쓰고(조회 중 선택지 고정), 없으면 저장 현황으로 새로 만든다.
    """
    repository = EquipmentRepository()
    with storage.transaction() as tx:
        groups = repository.rows(tx, "equipment_group")
        families = repository.rows(tx, "version_family")
        releases = repository.rows(tx, "release_version")
        equipment = {row["equipment_id"]: row for row in repository.rows(tx, "equipment")}
        stored = tuple(repository.load(tx, equipment[row["equipment_id"]], row)
                       for row in repository.rows(tx, "equipment_snapshot"))
        links = repository.rows(tx, "release_version_item")
        items = repository.rows(tx, "item")
        from setron.ui.item_original import original_item_rows
        original_rows = original_item_rows(tx, items)
        rules = repository.rows(tx, "risk_rule")
        pairs = tuple(sorted({(model_id, sw.family_id) for value in stored for sw in value.software
                              for model_id in ([pm.group_id for pm in sw.pms] if sw.pms else [value.group_id])
                              if model_id is not None}))
        target_catalog = TargetService(None).read_in(tx, pairs, {pair: None for pair in pairs})
        # 노후도 기간 기준 (BE-016). 저장값이 잘못되면 None이며 화면은 '기준 설정 오류'로 보인다.
        age_thresholds = thresholds_in(tx).thresholds
        extra_sites = registered_sites(tx)
    keys = group_keys(groups)
    catalog = catalog or database_catalog(stored, {group["group_name"] for group in groups},
                                          extra_groups=registered_group_options(groups, keys), extra_sites=extra_sites)

    # 개조형은 상위 설비군의 선택지에 함께 묶는다 (데이터 관리에서 등록한 개조형 포함, 11-11).
    bindings = tuple(GroupBinding(option.key, tuple(sorted(gid for gid, key in keys.items() if key == option.key)))
                     for option in catalog.groups)
    bound = {gid for binding in bindings for gid in binding.model_ids}
    excluded = {g["group_id"] for g in groups if not g["analysis_enabled"]}

    sites = {option.label: option.key for option in catalog.sites}
    records, skipped = [], 0
    for value in stored:
        try:
            record = read_equipment_record(value, excluded)
        except StorageValidationError:
            skipped += 1  # PM 연결이 잘못되거나 HPA·분석 모델이 섞인 현황은 임의로 고치지 않고 뺀다
            continue
        models = {pm.group_id for pm in record.pms}
        if record.site not in sites or (not record.is_hpa and not models <= bound):
            skipped += 1
            continue
        records.append(record)
    # 과거 관측을 나중에 가져와도 현재 위치는 snapshot ID가 아닌 관측일로 정한다.
    line_keys = {option.key for option in catalog.lines}
    fab_keys = {option.key for option in catalog.fabs}
    stored_areas = {(value.base_date, value.equipment_id): value.production_area for value in stored}

    def location(record):
        fab = stored_areas[record.base_date, record.equipment_id]
        fab = fab if fab in fab_keys else None
        key = _line_key(sites[record.site], fab, record.line)
        return EquipmentLocation(sites[record.site], record.line, fab, key if key in line_keys else None)

    locations = {r.equipment_id: location(r) for r in sorted(records, key=lambda record: record.base_date)}

    family_releases = families_from_rows(families, releases, {})
    item_catalog = ItemCatalog(family_releases, tuple(VersionItem(
        row["release_version_id"], row["item_id"], ItemState(row["item_state"]),
        TargetScope(row["target_scope"]), tuple(row["target_group_ids"] or ()),
    ) for row in links))
    assessor = RiskAssessor(family_releases, item_catalog,
                            tuple(rule_from_row(row, family_releases, links) for row in rules))

    def risk_status(record):
        """표시하는 모델의 PM만 포함한 관측을 현재 위험 규칙으로 다시 판정한다.

        param
        - record: 전체 또는 모델 필터를 적용한 설비 관측.

        return
        - 적용 가능한 위험 규칙의 판정. 기준 미등록·HPA는 미확인.
        """
        return (assessor.equipment_risk(record).status
                if assessor.rules and not record.is_hpa else RiskStatus.UNKNOWN)

    risks, risks_by_date = {}, {}
    for record in sorted(records, key=lambda value: value.base_date):
        status = risk_status(record)
        risks[record.equipment_id] = status
        risks_by_date[record.base_date, record.equipment_id] = status

    if assessor.rules:
        rule_state, rule_label = RiskRuleState.ACTIVE, f"{len(assessor.rules)}건 반영"
    elif rules:
        rule_state, rule_label = RiskRuleState.ALL_DISABLED, "모두 비활성"
    else:
        rule_state, rule_label = RiskRuleState.UNREGISTERED, "미등록"
    label = f"MySQL DB 저장 자료 · 모델별 목표 근거 확인 · 위험 기준 {rule_label} · 현황 {len(records)}건"
    if skipped:
        label += f" · Site·설비군 미연결 또는 확인 필요 {skipped}건 제외"
    source = DashboardSource(catalog=catalog, bindings=bindings, records=tuple(records), locations=locations,
                             families=family_releases, targets={}, risks=risks, source_label=label,
                             risk_evaluator=risk_status, risk_rule_state=rule_state,
                             locations_by_date={(r.base_date, r.equipment_id): location(r) for r in records})
    source.applicability = target_catalog.applicability
    # 버전(N) 기준 입력에서 저장한 N 그룹. 형식이 깨진 저장값은 그룹 없이(목표 N 하나로) 계산하고 안내한다.
    source.group_store = GroupStore(storage)
    try:
        source.saved_groups = source.group_store.load_many([(p.model_id, p.family_id) for p in target_catalog.pairs])
    except StorageValidationError as error:
        source.saved_groups = {}
        source.source_label += f" · 저장된 N 그룹 확인 필요({error})"
    source.target_reasons = {(pair.model_id, pair.family_id): pair.reason for pair in target_catalog.pairs}
    source.risks_by_date = risks_by_date
    # AI 분석·예측 입력(ui/analysis_data.py)이 대시보드와 같은 위험 판정·Item 자료를 쓰도록 함께 둔다.
    source.assessor, source.item_catalog = assessor, item_catalog
    # 화면이 번호 대신 이름을 보이도록 계열·모델 이름을 둔다 (dashboard_data.family_name·model_name).
    source.family_names = {row["family_id"]: row["family_name"] for row in families}
    source.age_thresholds = age_thresholds
    source.model_names = {row["group_id"]: row["group_name"] for row in groups}
    source.version_catalog = _version_catalog(groups, families, releases, items, links, stored,
                                             target_catalog.applicability, label)
    source.version_catalog = replace(source.version_catalog, original_rows=original_rows)
    return source


def _source_references(row):
    """저장된 원본 위치만 전달하고 보관하지 않은 문서명·리비전을 추정하지 않는다."""
    return tuple(SourceReference(field=field, sheet=location["sheet"],
                                 cells=", ".join(location["cells"]),
                                 batch_id=location.get("batch_id", row["batch_id"]))
                 for field, values in (row.get("source_reference") or {}).items()
                 for location in (values if isinstance(values, list) else [values]))


def _version_catalog(groups, families, releases, items, links, observations, applicability, label):
    """현재 저장 행으로 릴리즈·Item 읽기 계약을 구성한다.

    모델·계열 관계는 계열 소유 모델, 실제 관측, 명시된 Item 모델 범위와 저장된
    적용 확인에서만 가져온다. 같은 설비군이라는 이유로 다른 모델을 연결하지 않는다.
    """
    models = {row["group_id"]: row for row in groups}
    family_rows = {row["family_id"]: row for row in families}
    release_rows = {row["release_version_id"]: row for row in releases}
    pairs = {(row["group_id"], row["family_id"]) for row in families}
    pairs.update((model_id, software.family_id) for value in observations for software in value.software
                 for model_id in ([pm.group_id for pm in software.pms] if software.pms else [value.group_id])
                 if model_id is not None)
    pairs.update(applicability)
    for link in links:
        release = release_rows.get(link["release_version_id"])
        if release is not None:
            pairs.update((model_id, release["family_id"]) for model_id in (link["target_group_ids"] or ()))
    known = tuple(ModelFamily(model_id, models[model_id]["group_name"], family_id,
                              family_rows[family_id]["family_name"])
                  for model_id, family_id in sorted(pairs)
                  if model_id in models and family_id in family_rows)
    family_releases = families_from_rows(families, releases, {})
    entries = tuple(ReleaseEntry(
        family_releases[row["family_id"]].get(row["release_version_id"]),
        tuple(pair.model_id for pair in known if pair.family_id == row["family_id"]),
        _source_references(row), row["item_mapping_status"] == "VERIFIED",
    ) for row in releases)
    item_entries = tuple(ItemEntry(Item(row["item_id"], row["item_no"], row["content"]),
                                   _source_references(row)) for row in items)
    memberships = tuple(VersionItem(row["release_version_id"], row["item_id"], ItemState(row["item_state"]),
                                    TargetScope(row["target_scope"]), tuple(row["target_group_ids"] or ()))
                        for row in links)
    return VersionCatalog(known, entries, item_entries, memberships,
                          source_label=label + " · 저장된 릴리즈·Item·원본 위치")


class DatabaseDashboardSource(DashboardSource):
    """조회할 때마다 DB를 다시 읽는 대시보드 공급자.

    조회 중에는 선택지를 고정하고, 저장 뒤 :meth:`reload_catalog`로 새 Site·Line을 반영한다.
    ``loader(catalog)``는 ``catalog``가 None이면 저장 현황으로 선택지를 새로 만든다.
    """

    notice = SOURCE_LABEL  # 상단 안내용. 건수는 조회 결과마다 source_label에 다시 적힌다.
    result_label = "DB 자료"  # 화면 결과 배지 (합성 공급자는 "예시 결과")

    def __init__(self, loader: Callable[..., DashboardSource]) -> None:
        self._lock = RLock()
        self._loader = loader
        # 인자 없는 로더(고정 자료·기존 호출)는 선택지를 받지 않는다.
        self._takes_catalog = bool(inspect.signature(loader).parameters)
        self._copy(self._load(None))

    def _load(self, catalog):
        return self._loader(catalog) if self._takes_catalog else self._loader()

    def refresh(self) -> None:
        """DB를 다시 읽는다. 실패하면 이전 자료를 유지하고 화면이 처리하는 RuntimeError를 낸다."""
        with self._lock:
            try:
                fresh = self._load(self.catalog)
            except DatabaseError as error:
                raise RuntimeError(f"DB 자료를 읽지 못했습니다: {error}") from error
            # 선택지를 받지 않는 로더(고정 자료·테스트)는 로더가 만든 선택지를 그대로 쓴다.
            if fresh.bindings != self.bindings:
                raise RuntimeError("설비군 매핑이 바뀌었습니다. 앱을 다시 시작해 주세요.")
            self._copy(fresh)

    def reload_catalog(self) -> QueryCatalog:
        """저장 현황으로 조회 선택지를 다시 만들고 자료도 함께 다시 읽는다."""
        with self._lock:
            try:
                fresh = self._load(None)
            except DatabaseError as error:
                raise RuntimeError(f"DB 자료를 읽지 못했습니다: {error}") from error
            self._copy(fresh)
            return self.catalog

    def load_version_catalog(self) -> VersionCatalog:
        """새 DB 자료의 릴리즈·Item·원본 위치를 조회 화면에 전달한다."""
        with self._lock:
            self.refresh()
            return self.version_catalog

    def load_item_data(self):
        """같은 DB 읽기의 Item·릴리즈·관측을 다른 워커의 갱신과 섞지 않고 반환한다."""
        from setron.ui.item_data import item_data_from_source

        with self._lock:
            self.refresh()
            return item_data_from_source(self.version_catalog, self)

    def snapshot(self, query):
        """최신 모델 적용 근거로 N을 검사하고 원래 선택 ID는 변경하지 않는다.

        param
        - query: 사용자가 적용한 위치·모델별 N 조건.

        return
        - 현재 자료로 계산한 결과. 무효 N은 계산에서 제외하고 재선택 이유를 표시.
        """
        with self._lock:
            self.refresh()
            if query.targets is None:
                return super().snapshot(query)
            selected = self.align_targets(query.targets)
            pairs = {pair.key: pair for pair in target_pairs(self)}
            invalid = tuple(target for target in selected if target.release_id is not None and
                            target.release_id not in {option.release.release_version_id for option in pairs[
                                target.model_id, target.family_id].options if option.unavailable_reason is None})
            invalid_keys = {(target.model_id, target.family_id) for target in invalid}
            effective = tuple(replace(target, release_id=None) if (target.model_id, target.family_id) in invalid_keys
                              else target for target in selected)
            result = super().snapshot(replace(query, targets=effective))
            if invalid:
                notice = " / ".join(f"{model_name(self, target.model_id)}·계열 {family_name(self, target.family_id)}"
                                    for target in invalid)
                result = replace(result, source_label=f"{result.source_label} · 목표 N 재선택·적용 근거 재확인 필요: {notice}")
            # 화면은 요청한 조회 객체와 결과를 비교하므로 원래 조회 객체를 돌려준다.
            return replace(result, query=query)

    def align_targets(self, targets):
        """다시 읽은 자료의 모델·계열 목록에 목표 초안을 맞춘다.

        이미 고른 목표는 그대로 두고, 새로 생긴 모델·계열은 미지정, 사라진 것은 뺀다.
        """
        with self._lock:
            chosen = {(t.model_id, t.family_id): t.release_id for t in targets}
            return tuple(TargetN(p.model_id, p.family_id, chosen.get(p.key, p.current_id)) for p in target_pairs(self))

    def _copy(self, source: DashboardSource) -> None:
        with self._lock:
            self.__dict__.update({k: v for k, v in vars(source).items() if k not in {"_lock", "_loader", "_takes_catalog"}})


def dashboard_source_from_env(environ=None) -> tuple[DatabaseDashboardSource | None, str | None]:
    """``SETRON_DB_*`` 설정이 있으면 DB 공급자를 만든다.

    return
    - (공급자, None): DB 자료를 쓴다.
    - (None, None): 설정이 없어 합성 예시를 쓴다.
    - (None, 안내): 설정은 있지만 읽지 못해 합성 예시로 시작한다.
    """
    import os

    from setron.features.storage.service import StorageService
    from setron.infrastructure.database import Database

    values = os.environ if environ is None else environ
    if not values.get("SETRON_DB_NAME"):
        return None, None
    try:
        storage = StorageService(Database.from_env(values))
        return DatabaseDashboardSource(lambda catalog=None: load_dashboard_source(storage, catalog)), None
    except (DatabaseError, StorageValidationError) as error:
        return None, f"DB 자료를 읽지 못해 합성 예시로 시작했습니다: {error}"


def sql_exporter_from_env(environ=None):
    """``SETRON_DB_*`` 설정이 있으면 ``path``를 받아 DB 전체를 .sql로 저장하는 함수를, 없으면 None을 돌려준다.

    DB 접속은 내보낼 때 하고, 내보내기가 끝나면 연결 풀을 정리한다.
    """
    import os
    from pathlib import Path

    from setron.features.db_export.service import export_database
    from setron.features.export.schemas import ExportResult, ExportStatus
    from setron.infrastructure.database import Database

    values = os.environ if environ is None else environ
    if not values.get("SETRON_DB_NAME"):
        return None

    def exporter(path):
        database = None
        try:
            database = Database.from_env(values)
            return export_database(database, path)
        except DatabaseError as error:
            return ExportResult(ExportStatus.FAILED, Path(path), f"DB 자료를 읽지 못했습니다: {error}")
        finally:
            if database is not None:
                database.dispose()

    return exporter


def sql_importer_from_env(environ=None):
    """설정된 DB에서 SETRON SQL 파일을 비교하고 저장할 서비스를 만든다."""
    import os
    from functools import partial

    from setron.features.db_import.service import SqlImportService
    from setron.infrastructure.database import Database

    values = os.environ if environ is None else environ
    if not values.get("SETRON_DB_NAME"):
        return None
    return SqlImportService(partial(Database.from_env, values))


def data_exporter_from_env(environ=None):
    """``SETRON_DB_*`` 설정이 있으면 ``path``를 받아 관리 데이터를 .xlsx로 저장하는 함수를, 없으면 None을 돌려준다.

    DB 접속은 내보낼 때 하고, 내보내기가 끝나면 연결 풀을 정리한다.
    """
    import os
    from pathlib import Path

    from setron.features.export.management import save_management_summary
    from setron.features.export.schemas import ExportResult, ExportStatus
    from setron.features.storage.service import StorageService
    from setron.infrastructure.database import Database

    values = os.environ if environ is None else environ
    if not values.get("SETRON_DB_NAME"):
        return None

    def exporter(path):
        database = None
        try:
            database = Database.from_env(values)
            return save_management_summary(StorageService(database), path)
        except DatabaseError as error:
            return ExportResult(ExportStatus.FAILED, Path(path), f"DB 자료를 읽지 못했습니다: {error}")
        finally:
            if database is not None:
                database.dispose()

    return exporter
