"""정확한 관측일의 저장 자료로 조회 목록을 만든다. 현재 소속이나 원문으로 보충하지 않는다."""

from collections.abc import Iterable, Mapping
from datetime import date

from sqlalchemy import select

from setron.features.equipment.read import read_pm_versions
from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.schemas import StoredSnapshot
from setron.features.storage.inputs import require

from .schemas import FamilyOption, LineOption, ModelOption, QueryMatch, QueryOptions, QueryScope


class QueryOptionsService:
    """공통 저장 트랜잭션에서 기준일과 그 날짜의 선택 목록을 함께 읽는다."""

    def __init__(self, storage):
        self.storage = storage
        self.repository = EquipmentRepository()

    def read(self, scope: QueryScope | None = None) -> QueryOptions:
        """``None``이면 날짜 목록만 반환한다. 데이터가 없는 날짜는 빈 목록이다."""
        with self.storage.transaction() as tx:
            return self.read_in(tx, scope)

    def read_in(self, tx, scope: QueryScope | None = None) -> QueryOptions:
        """기존 트랜잭션에서 읽어 N 후보 등과 같은 시점의 조회 목록을 제공한다."""
        scope = QueryScope() if scope is None else scope
        require(isinstance(scope, QueryScope), "조회 범위 형식이 올바르지 않습니다.")
        table = tx.repository.table("equipment_snapshot")
        dates = tuple(tx.session.execute(select(table.c.base_date).distinct().order_by(table.c.base_date)).scalars())
        groups = self.repository.rows(tx, "equipment_group")
        families = self.repository.rows(tx, "version_family")
        snapshots = []
        if scope.base_date is not None:
            rows = self.repository.rows(tx, "equipment_snapshot", base_date=scope.base_date)
            equipment = {}
            for row in rows:
                equipment_id = row["equipment_id"]
                if equipment_id not in equipment:
                    equipment[equipment_id] = tx.get("equipment", {"equipment_id": equipment_id})
                snapshots.append(self.repository.load(tx, equipment[equipment_id], row))
        from setron.features.data_admin.service import registered_lines
        return build_query_options(snapshots, groups, families, scope, available_dates=dates,
                                   line_entries=registered_lines(tx))


def build_query_options(
    snapshots: Iterable[StoredSnapshot],
    groups: Iterable[Mapping],
    families: Iterable[Mapping],
    scope: QueryScope,
    *,
    available_dates: Iterable[date] | None = None,
    line_entries=(),
) -> QueryOptions:
    """저장 조회 결과에서 목록을 만드는 순수 함수. 입력 자료를 변경하지 않는다.

    목록은 모든 선택 조건을 만족하는 관측과 SW/PM 연결에서 만든다. 같은 C3 UI의
    형상 원문은 목록의 키로 쓰지 않으며 다른 UI의 실제 계열 ID는 분리해 보존한다.
    ``available_dates``를 생략하면 전달된 관측의 날짜만 나열한다.
    """
    require(isinstance(scope, QueryScope), "조회 범위 형식이 올바르지 않습니다.")
    snapshots = tuple(snapshots)
    require(all(isinstance(row, StoredSnapshot) for row in snapshots), "저장된 현황 조회 결과가 필요합니다.")
    days = tuple(available_dates) if available_dates is not None else tuple(row.base_date for row in snapshots)
    require(all(type(day) is date for day in days), "저장된 기준일 형식이 잘못됐습니다.")
    dates = tuple(sorted(set(days)))
    if scope.base_date is None:
        return QueryOptions(scope, dates)
    group_rows = tuple(groups)
    family_rows = tuple(families)
    group_map = {row["group_id"]: row for row in group_rows}
    family_map = {row["family_id"]: row for row in family_rows}
    require(len(group_map) == len(group_rows), "등록 모델 ID가 중복되었습니다.")
    require(len(family_map) == len(family_rows), "등록 계열 ID가 중복되었습니다.")
    result, seen, selected_models, selected_pairs = [], set(), set(), set()
    from setron.features.hierarchy import line_parts
    for snapshot in sorted(snapshots, key=lambda row: (row.base_date, row.equipment_id, row.snapshot_id)):
        require(type(snapshot.base_date) is date, "저장된 기준일 형식이 잘못됐습니다.")
        if snapshot.base_date != scope.base_date:
            continue
        identity = snapshot.equipment_id, snapshot.base_date
        require(identity not in seen, "같은 설비·기준일의 현황이 중복되었습니다.")
        seen.add(identity)
        if scope.sites and snapshot.site not in scope.sites:
            continue
        parent, subline = line_parts(snapshot.site, snapshot.line, line_entries)
        line_names = {snapshot.line} | ({parent, subline} if subline is not None else set())
        if scope.lines and not line_names.intersection(scope.lines):
            continue
        if scope.site_lines and not any(LineOption(snapshot.site, name) in scope.site_lines for name in line_names):
            continue
        pms = read_pm_versions(snapshot)
        pairs = {(pm.group_id, pm.family_id) for pm in pms}
        for model_id, family_id in pairs:
            require(model_id in group_map, "현황의 모델이 등록 자료에 없습니다.")
            require(family_id in family_map, "현황의 계열이 등록 자료에 없습니다.")
        # 한 PM의 모델과 다른 PM의 계열을 교차 결합하지 않는다.
        matching_pairs = tuple(sorted((model_id, family_id) for model_id, family_id in pairs
            if (not scope.model_ids or model_id in scope.model_ids)
            and (not scope.family_ids or family_id in scope.family_ids)
            and (not scope.categories or group_map[model_id]["category"] in scope.categories)))
        if pairs and not matching_pairs:
            continue
        model_ids = {model_id for model_id, _ in matching_pairs}
        if not pairs:
            model_id = snapshot.group_id
            if model_id is not None:
                require(model_id in group_map, "현황의 모델이 등록 자료에 없습니다.")
            if scope.family_ids or (scope.model_ids and model_id not in scope.model_ids):
                continue
            if scope.categories and (model_id is None or group_map[model_id]["category"] not in scope.categories):
                continue
            if model_id is not None:
                model_ids.add(model_id)
        selected_models.update(model_ids)
        selected_pairs.update(matching_pairs)
        result.append(QueryMatch(snapshot, matching_pairs))
    selected_families = {family_id for _, family_id in selected_pairs}
    models = tuple(ModelOption(model_id, group_map[model_id]["group_name"], group_map[model_id]["category"],
                               group_map[model_id].get("parent_group_id"), bool(group_map[model_id]["analysis_enabled"]))
                   for model_id in sorted(selected_models))
    family_options = tuple(FamilyOption(family_id, family_map[family_id]["family_name"],
                                        family_map[family_id].get("ui_type"), family_map[family_id]["group_id"])
                           for family_id in sorted(selected_families))
    line_values = {(match.snapshot.site, match.snapshot.line) for match in result}
    return QueryOptions(
        scope=scope, dates=dates,
        sites=tuple(sorted({match.snapshot.site for match in result})),
        lines=tuple(LineOption(site, line) for site, line in sorted(line_values, key=lambda value: (value[0], value[1] is not None, value[1]))),
        categories=tuple(sorted({model.category for model in models})),
        models=models, families=family_options, model_family_pairs=tuple(sorted(selected_pairs)), matches=tuple(result),
    )
