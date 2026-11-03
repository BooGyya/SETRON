"""DB에 저장된 설비 현황·릴리즈로 대시보드 공급자를 만든다 (Jira S15P31B106-75).

대시보드·HeatMap·버전·Item 화면이 같은 :class:`DashboardSource`를 쓰므로, 이 공급자 하나로 조회 화면 전체가
DB 자료를 본다. '조회 적용'을 누를 때마다 DB를 다시 읽어 가져오기로 저장한 자료가 바로 반영된다.

정한 규칙
- 조회 선택지(Site·설비군)는 앱 시작 시의 VER3 선택지를 그대로 쓴다. Site는 이름이 같을 때만, 설비군은
  :data:`GROUP_MODELS`의 등록 모델 이름으로만 연결한다. 연결되지 않는 현황은 빼고 건수를 안내한다.
- HPA는 모델의 analysis_enabled = false로만 판단한다 (:func:`read_equipment_records`).
- 목표 N과 위험 기준은 아직 DB에 저장하지 않으므로 지정하지 않는다. 적용률은 '목표 미지정'으로 계산 불가,
  위험은 미확인으로 표시된다. 목표 N은 화면의 '목표 기준 편집'에서 순서가 확인된 릴리즈로 정할 수 있다.
- 원본 구역(raw_area)은 기준일의 Line 원문을 쓴다. Fab/Line 선택지로는 추정하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from setron.features.equipment.repository import EquipmentRepository
from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.features.storage.validation import StorageValidationError
from setron.infrastructure.database import DatabaseError
from setron.ui.dashboard_data import DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.query_state import QueryCatalog, TargetN, preview_catalog
from setron.ui.read_converters import read_equipment_record
from setron.ui.target_data import target_pairs

# UI 설비군 키 → 등록 모델 이름. 시연용 기준 자료(database/seed_demo_catalog.sql)의 이름과 같다.
GROUP_MODELS = {
    "c3": ("M-C3",), "c4": ("M-C4",), "o2": ("M-O2",), "o3": ("M-O3",), "o5": ("M-O5", "M-O5 Prime"),
    "inline-ox": (), "inline-o2": ("Inline O2",), "cluster-ox": (), "cluster-o2": ("Cluster O2",),
}
SOURCE_LABEL = "MySQL DB 저장 자료 · 목표 N·위험 기준 미등록"


def load_dashboard_source(storage, catalog: QueryCatalog | None = None) -> DashboardSource:
    """DB를 한 번 읽어 대시보드 공급자를 만든다. 연결할 수 없는 현황은 빼고 ``source_label``에 건수를 적는다."""
    catalog = catalog or preview_catalog()
    repository = EquipmentRepository()
    with storage.transaction() as tx:
        groups = repository.rows(tx, "equipment_group")
        families = repository.rows(tx, "version_family")
        releases = repository.rows(tx, "release_version")
        equipment = {row["equipment_id"]: row for row in repository.rows(tx, "equipment")}
        stored = tuple(repository.load(tx, equipment[row["equipment_id"]], row)
                       for row in repository.rows(tx, "equipment_snapshot"))

    by_name = {}
    for group in groups:
        by_name.setdefault(group["group_name"], []).append(group["group_id"])
    bindings = tuple(GroupBinding(option.key, tuple(sorted(
        gid for name in GROUP_MODELS.get(option.key, ()) for gid in by_name.get(name, ()))))
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
    locations = {r.equipment_id: EquipmentLocation(sites[r.site], r.line) for r in records}

    family_releases = {f["family_id"]: FamilyReleases(f["family_id"], tuple(
        ReleaseVersion(r["release_version_id"], r["family_id"], r["version_raw"], r["version_order"], r["released_at"])
        for r in releases if r["family_id"] == f["family_id"])) for f in families}

    label = f"{SOURCE_LABEL} · 현황 {len(records)}건"
    if skipped:
        label += f" · Site·설비군 미연결 또는 확인 필요 {skipped}건 제외"
    return DashboardSource(catalog=catalog, bindings=bindings, records=tuple(records), locations=locations,
                           families=family_releases, targets={},
                           risks={r.equipment_id: RiskStatus.UNKNOWN for r in records}, source_label=label)


class DatabaseDashboardSource(DashboardSource):
    """조회할 때마다 DB를 다시 읽는 대시보드 공급자. 조회 선택지와 설비군 매핑은 처음 값을 유지한다."""

    notice = SOURCE_LABEL  # 상단 안내용. 건수는 조회 결과마다 source_label에 다시 적힌다.

    def __init__(self, loader: Callable[[], DashboardSource]) -> None:
        self._loader = loader
        self._copy(loader())

    def refresh(self) -> None:
        """DB를 다시 읽는다. 실패하면 이전 자료를 유지하고 화면이 처리하는 RuntimeError를 낸다."""
        try:
            fresh = self._loader()
        except DatabaseError as error:
            raise RuntimeError(f"DB 자료를 읽지 못했습니다: {error}") from error
        if fresh.catalog != self.catalog or fresh.bindings != self.bindings:
            raise RuntimeError("조회 선택지나 설비군 매핑이 바뀌었습니다. 앱을 다시 시작해 주세요.")
        self._copy(fresh)

    def snapshot(self, query):
        self.refresh()
        if query.targets is None:
            return super().snapshot(query)
        result = super().snapshot(replace(query, targets=self.align_targets(query.targets)))
        # 화면은 요청한 조회 객체와 결과를 비교하므로 원래 조회 객체를 돌려준다.
        return replace(result, query=query)

    def align_targets(self, targets):
        """다시 읽은 자료의 모델·계열 목록에 목표 초안을 맞춘다.

        이미 고른 목표는 그대로 두고, 새로 생긴 모델·계열은 미지정, 사라진 것은 뺀다.
        """
        chosen = {(t.model_id, t.family_id): t.release_id for t in targets}
        return tuple(TargetN(p.model_id, p.family_id, chosen.get(p.key, p.current_id)) for p in target_pairs(self))

    def _copy(self, source: DashboardSource) -> None:
        self.__dict__.update({k: v for k, v in vars(source).items()})


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
        return DatabaseDashboardSource(lambda: load_dashboard_source(storage)), None
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
