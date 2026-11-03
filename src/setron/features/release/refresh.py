"""릴리즈 변경과 같은 트랜잭션에서 등록 연결·목표 유효성·업무 판정을 갱신한다."""

from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from sqlalchemy import select

from setron.features.aggregation.service import summarize
from setron.features.equipment.read import read_equipment_records
from setron.features.equipment.repository import EquipmentRepository
from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.features.item.service import ItemCatalog
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.features.risk.service import RiskAssessor, validate_rule
from setron.features.storage.inputs import identifier, require, required_text
from setron.features.storage.validation import StorageValidationError, decode_record
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases, sort_releases

from .refresh_schemas import DateJudgments, RefreshContext, ReleaseRefresh, TargetValidity


class _ExactFamilyReleases(FamilyReleases):
    """새 연결 판정에서도 원문 또는 명시적으로 확인된 표기만 사용한다."""

    def __init__(self, family_id, releases, aliases):
        """공통 비교 자료와 정확한 원문 조회표를 준비한다.

        저장된 버전은 앞뒤 공백까지 다른 원문이다. 공백을 제거하는 부모 생성자 대신
        정확한 원문으로 중복을 검사하고, 순서 계산과 ID 조회 계약은 그대로 유지한다.

        param
        - family_id, releases: 같은 계열의 현재 릴리즈 ID와 원문·순서 자료.
        - aliases: 호출자가 확인한 정확한 다른 표기와 릴리즈 원문의 대응.

        raises
        - ValueError: 공통 비교 자료의 릴리즈 원문이 상충하는 경우.
        """
        releases = tuple(releases)
        if any(release.family_id != family_id for release in releases):
            raise ValueError(f"계열 {family_id}가 아닌 릴리즈가 섞여 있습니다.")
        self.family_id = family_id
        self.releases = releases
        self.sorted = sort_releases(releases)
        self._by_id = {release.release_version_id: release for release in releases}
        self._by_raw = {}
        for release in releases:
            if release.version_raw in self._by_raw:
                raise ValueError(f"계열 {family_id}에 정확히 같은 원문의 릴리즈가 두 개 있습니다.")
            self._by_raw[release.version_raw] = release
        self._aliases = {}
        for alias, raw in aliases.items():
            if raw not in self._by_raw:
                raise ValueError(f"확정 별칭 {alias}의 대상 원문이 계열 {family_id}에 없습니다.")
            self._aliases[alias] = self._by_raw[raw]
        self._exact_raws = self._by_raw
        self._exact_aliases = self._aliases

    def match(self, version_raw):
        """앞뒤 공백도 확인되지 않은 표기로 보고 자동 대응하지 않는다.

        param
        - version_raw: 원본 설비 버전 문자열. None이면 미등록으로 반환.

        return
        - 정확한 원문 또는 확인한 별칭의 릴리즈. 대응이 없으면 None.
        """
        return self._exact_raws.get(version_raw) or self._exact_aliases.get(version_raw)


class ReleaseRefreshService:
    """기존 목표를 바꾸거나 AI를 호출하지 않고 현재 자료로 다시 판정한다."""

    def refresh_in(self, tx, *, context: RefreshContext = RefreshContext(),
                   family_ids: tuple[int, ...] | None = None,
                   invalidated_release_ids: tuple[int, ...] = ()) -> ReleaseRefresh:
        """현재 저장 업무 안에서 릴리즈 연결과 모든 관측일의 판정을 갱신한다.

        이미 확인한 같은 계열의 연결 ID는 원문이 다르더라도 보존한다. 원문을 수정한
        릴리즈 ID는 invalidated_release_ids로 전달해 정확한 현재 원문 또는 확인된
        별칭으로 다시 연결한다. 원본 버전·PM·입력 작업은 수정하지 않는다.

        param
        - tx: 등록·수정·교체가 공유하는 StorageTransaction. 확정·취소는 호출자가 관리.
        - context: 기존 목표와 호출자가 확인한 모델별 적용 범위·버전 별칭.
        - family_ids: 적용 SW 연결을 다시 검사할 계열 ID 튜플. None이면 모든 계열.
        - invalidated_release_ids: 원문 변경 등으로 이전 연결 증거가 무효인 릴리즈 ID 튜플.

        return
        - 기존 목표의 유효성과 기준일별 공통 집계·Item 적용·위험 판정.

        raises
        - StorageValidationError: 조건 형식이나 저장된 관측 연결이 잘못된 경우.
        - ValueError: 릴리즈 원문 또는 확인된 별칭 대응이 상충하는 경우.
        """
        context = _context_copy(context)
        _ids(family_ids, "family_ids", optional=True)
        _ids(invalidated_release_ids, "invalidated_release_ids")
        repository = EquipmentRepository()
        groups = {row["group_id"]: row for row in repository.rows(tx, "equipment_group")}
        family_rows = repository.rows(tx, "version_family")
        release_rows = repository.rows(tx, "release_version")
        families = _families(family_rows, release_rows, context.aliases)
        affected = set(families) if family_ids is None else set(family_ids)
        require(affected <= set(families), "재판정 대상에 미등록 계열이 있습니다.")
        _link_equipment(tx, repository, families, release_rows, context.aliases,
                        affected, set(invalidated_release_ids))

        owners = {row["family_id"]: row["group_id"] for row in family_rows}
        validity = tuple(_target_validity(pair, release_id, families, groups, owners, context.applicability)
                         for pair, release_id in sorted(context.targets.items()))
        effective_targets = {(target.model_id, target.family_id): target.selected_release_id
                             for target in validity if target.valid}
        links = repository.rows(tx, "release_version_item")
        catalog = ItemCatalog(families, tuple(VersionItem(
            row["release_version_id"], row["item_id"], ItemState(row["item_state"]),
            TargetScope(row["target_scope"]), tuple(row["target_group_ids"] or ()),
        ) for row in links))
        items = tuple(Item(row["item_id"], row["item_no"], row["content"])
                      for row in repository.rows(tx, "item"))
        rules = tuple(_rule(row, families, links) for row in repository.rows(tx, "risk_rule"))
        assessor = RiskAssessor(families, catalog, rules)

        equipment = {row["equipment_id"]: row for row in repository.rows(tx, "equipment")}
        snapshots = tuple(repository.load(tx, equipment[row["equipment_id"]], row)
                          for row in repository.rows(tx, "equipment_snapshot"))
        excluded = {gid for gid, group in groups.items() if not group["analysis_enabled"]}
        by_date = defaultdict(list)
        for record in read_equipment_records(snapshots, excluded):
            by_date[record.base_date].append(record)
        dates = tuple(DateJudgments(
            base_date=day, summary=summarize(by_date[day], families, effective_targets),
            item_summaries=tuple(catalog.summarize(item, by_date[day]) for item in items),
            risks=assessor.summarize(by_date[day]),
        ) for day in sorted(by_date))
        return ReleaseRefresh(families=families, targets=validity, dates=dates,
                              unresolved_risk_ids=tuple(rule.risk_rule_id for rule in rules
                                                       if rule.mapping_status is MappingStatus.UNRESOLVED))


def _ids(values, name, *, optional=False):
    """선택 범위 ID 튜플의 자료형과 중복을 검사한다.

    param
    - values, name: 검사할 ID 튜플과 오류 표시 이름.
    - optional: True이면 None을 전체 선택으로 허용.

    raises
    - StorageValidationError: 튜플이 아니거나 잘못된 ID·중복 ID가 있는 경우.
    """
    if optional and values is None:
        return
    require(isinstance(values, tuple), f"{name}: ID 튜플이 필요합니다.")
    for value in values:
        identifier(value, name)
    require(len(set(values)) == len(values), f"{name}: ID가 중복됐습니다.")


def _context_copy(context):
    """호출자의 조건을 검증하고 판정 중 변경되지 않도록 복사한다.

    param
    - context: 기존 목표와 확인된 대응을 포함한 RefreshContext.

    return
    - 호출자의 매핑과 분리한 조건 복사본.

    raises
    - StorageValidationError: ID·범위·별칭 대응 형식이 잘못된 경우.
    """
    require(isinstance(context, RefreshContext), "RefreshContext 형식이 필요합니다.")
    for values in (context.targets, context.applicability, context.aliases):
        require(isinstance(values, Mapping), "재판정 조건은 매핑으로 전달해야 합니다.")
    for mapping in (context.targets, context.applicability):
        for pair in mapping:
            require(isinstance(pair, tuple) and len(pair) == 2, "모델·계열 ID 쌍이 필요합니다.")
            identifier(pair[0], "model_id")
            identifier(pair[1], "family_id")
    for selected in context.targets.values():
        if selected is not None:
            identifier(selected, "selected_release_id")
    for releases in context.applicability.values():
        _ids(releases, "applicability")
    for fid, aliases in context.aliases.items():
        identifier(fid, "family_id")
        require(isinstance(aliases, Mapping), "계열별 별칭 매핑이 필요합니다.")
        for alias, raw in aliases.items():
            required_text(alias, "alias", 100)
            required_text(raw, "version_raw", 100)
    return RefreshContext(deepcopy(dict(context.targets)), deepcopy(dict(context.applicability)),
                          {fid: deepcopy(dict(aliases)) for fid, aliases in context.aliases.items()})


def _families(family_rows, release_rows, aliases):
    """전체 계열을 현재 릴리즈와 유효한 확정 별칭으로 구성한다.

    param
    - family_rows, release_rows: 같은 저장 트랜잭션에서 읽은 계열·릴리즈 행.
    - aliases: 호출자가 확인한 계열별 별칭 대응. 삭제된 대상은 연결하지 않음.

    return
    - 계열 ID별 FamilyReleases.
    """
    result = {}
    for family in family_rows:
        fid = family["family_id"]
        rows = tuple(row for row in release_rows if row["family_id"] == fid)
        raws = {row["version_raw"] for row in rows}
        confirmed = {alias: raw for alias, raw in aliases.get(fid, {}).items() if raw in raws}
        try:
            result[fid] = _ExactFamilyReleases(fid, tuple(ReleaseVersion(
                row["release_version_id"], fid, row["version_raw"], row["version_order"], row["released_at"])
                for row in rows), confirmed)
        except ValueError as exc:
            raise StorageValidationError(f"계열 {fid}: 릴리즈 원문·별칭 대응이 모호합니다. {exc}") from exc
    return result


def _link_equipment(tx, repository, families, release_rows, aliases, affected, invalidated):
    """확인된 기존 연결은 유지하고 무효·미연결 SW만 정확한 대응으로 다시 연결한다.

    param
    - tx, repository: 현재 저장 트랜잭션과 공통 행 조회 레포지토리.
    - families, release_rows: 현재 계열별 릴리즈와 원문 행.
    - aliases: 계열별로 확인한 정확한 다른 표기 대응.
    - affected, invalidated: 검사할 계열 ID 집합과 이전 연결이 무효인 릴리즈 ID 집합.
    """
    by_raw = {(row["family_id"], row["version_raw"]): row["release_version_id"] for row in release_rows}
    table = tx.repository.table("equipment_version")
    query = select(table).where(table.c.family_id.in_(sorted(affected))).order_by(
        table.c.equipment_version_id).with_for_update()
    # 현황 재가져오기와 겹치더라도 잠금 전에 읽은 예전 원문으로 새 연결을 덮어쓰지 않는다.
    for saved in tx.session.execute(query).mappings():
        row = decode_record("equipment_version", dict(saved))
        fid, previous = row["family_id"], row["release_version_id"]
        if fid not in affected:
            continue
        if previous is not None and previous not in invalidated and families[fid].get(previous) is not None:
            continue
        raw = row["version_raw"]
        linked = by_raw.get((fid, raw))
        if linked is None:
            linked = by_raw.get((fid, aliases.get(fid, {}).get(raw)))
        if linked != previous:
            tx.update("equipment_version", {"equipment_version_id": row["equipment_version_id"]},
                      {"release_version_id": linked})


def _target_validity(pair, selected, families, groups, owners, applicability):
    """기존 N의 계열·순서·모델 적용 범위를 검사하고 재선택 이유를 만든다.

    param
    - pair, selected: 모델·계열 ID 쌍과 기존 목표 ID.
    - families, groups, owners: 현재 릴리즈 자료·등록 모델·계열 관리 모델 ID.
    - applicability: 호출자가 확인한 모델·계열별 적용 가능 릴리즈.

    return
    - 기존 목표를 자동 변경하지 않은 유효성 결과.
    """
    model_id, fid = pair
    family = families.get(fid)
    release = family.get(selected) if family is not None and selected is not None else None
    reason = None
    if model_id not in groups:
        reason = "모델이 등록 자료에 없습니다. 모델과 목표를 다시 선택해 주세요."
    elif fid in owners and groups[model_id]["category"] != groups[owners[fid]]["category"]:
        reason = "모델과 목표 계열의 설비군이 다릅니다. 모델과 목표를 다시 선택해 주세요."
    elif selected is None:
        reason = "목표가 미지정입니다. 확인된 목표를 선택해 주세요."
    elif release is None:
        reason = "목표가 같은 계열의 현재 릴리즈에 없습니다. 목표를 다시 선택해 주세요."
    elif release.version_order is None:
        reason = "목표 릴리즈의 순서가 미확인입니다. 목표를 다시 선택해 주세요."
    elif Counter(r.version_order for r in family.releases)[release.version_order] > 1:
        reason = "목표 릴리즈의 순서가 중복됐습니다. 순서를 확인하고 목표를 다시 선택해 주세요."
    elif pair not in applicability:
        reason = "해당 모델의 적용 가능 버전이 미확인입니다. 적용 범위를 확인한 뒤 목표를 다시 선택해 주세요."
    elif selected not in applicability[pair]:
        reason = "해당 모델에 적용 가능한 목표가 아닙니다. 목표를 다시 선택해 주세요."
    return TargetValidity(model_id, fid, selected, reason is None, reason)


def _rule(row, families, links):
    """수동 위험 규칙을 변환하고 잘못된 연결은 이번 판정에서 미확인으로 둔다.

    기존 수동 지정과 원본 근거를 변경하지 않는다. 미확인 해제 버전을 미해제와
    혼동하지 않도록 규칙 전체를 UNRESOLVED로 판정한다.

    param
    - row: 위험 규칙의 현재 저장 행.
    - families, links: 현재 계열별 릴리즈와 버전별 Item 연결 행.

    return
    - 연결·범위 검증을 반영한 RiskRule. DB 행은 수정하지 않음.
    """
    names = ("risk_rule_id", "family_id", "risk_reason", "group_id", "item_id", "release_version_id",
             "start_release_version_id", "end_release_version_id", "is_enabled")
    rule = RiskRule(**{name: row[name] for name in names}, rule_type=RuleType(row["rule_type"]),
                    mapping_status=MappingStatus(row["mapping_status"]))
    invalid = bool(validate_rule(rule, families))
    family = families.get(rule.family_id)
    if rule.item_id is not None:
        invalid |= family is None or not any(
            link["item_id"] == rule.item_id and family.get(link["release_version_id"]) is not None
            for link in links
        )
    if family is not None:
        for selected in (rule.start_release_version_id, rule.end_release_version_id):
            if selected is not None:
                release = family.get(selected)
                invalid |= (release is None or release.version_order is None
                            or release.version_order in family.sorted.duplicated_orders)
    return replace(rule, mapping_status=MappingStatus.UNRESOLVED) if invalid else rule
