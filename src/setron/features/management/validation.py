"""직접 수정한 현재 관리값과 연결을 원본 자료를 바꾸지 않고 검사한다."""

from collections import Counter
from collections.abc import Mapping

from setron.features.equipment.read import read_equipment_record
from setron.features.equipment.repository import EquipmentRepository
from setron.features.equipment.service import EquipmentService
from setron.features.equipment.validation import prepare_snapshot
from setron.features.release.refresh import _context_copy, _families
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.risk.schemas import MappingStatus, RiskRule, RuleType
from setron.features.risk.service import validate_rule
from setron.features.storage.inputs import identifier, require, required_text
from setron.features.storage.validation import encode_record, normalize_item_jira


_SOFTWARE_FIELDS = frozenset({"family_id", "version_raw", "release_version_id"})
_EXCEL_ERRORS = ("#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!")


def validate_changes_in(tx, table, current, changes):
    """수정 전에 현재값과 변경값을 합쳐 자료형과 BE-008 입력 형식을 검사한다.

    PM의 가상 SW 필드는 연결된 SW 행의 정의로 따로 검사한다. 기존 관리값의
    표기를 임의로 고치지 않으며 문자열 형식 검사는 이번에 수정하는 값에만 적용한다.
    """
    require(isinstance(current, Mapping) and isinstance(changes, Mapping),
            "현재 관리값과 수정할 항목이 필요합니다.")
    if table == "item":
        current, changes = normalize_item_jira(current), normalize_item_jira(changes)
    if table == "equipment_pm":
        pm = {field: value for field, value in current.items() if field not in _SOFTWARE_FIELDS}
        pm.update({field: value for field, value in changes.items() if field not in _SOFTWARE_FIELDS})
        _validate_record_in(tx, table, pm)
        software = tx.get("equipment_version", {"equipment_version_id": current["equipment_version_id"]})
        software.update({field: value for field, value in changes.items() if field in _SOFTWARE_FIELDS})
        _validate_record_in(tx, "equipment_version", software)
    else:
        _validate_record_in(tx, table, {**current, **changes})

    for field, value in changes.items():
        if not isinstance(value, str):
            continue
        if field in {"equipment_code", "version_raw"}:
            require("\n" not in value and "\r" not in value,
                    f"{field}: 원문에 줄바꿈이 있습니다. 형식 오류입니다.")
        require(not value.startswith(_EXCEL_ERRORS), f"{field}: Excel 오류값을 보완해야 합니다.")
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            require(False, f"{field}: UTF-8로 저장할 수 없는 문자입니다.")
        if field in {"content", "risk_reason"}:
            require(len(encoded) <= 65535, f"{field}: 저장 가능한 크기(65535바이트)를 넘었습니다.")


def _validate_record_in(tx, table, row):
    encoded = encode_record(table, row)
    definition = tx.repository.table(table)
    encoded = tx.repository._legacy_jira_values(definition, encoded)
    tx.repository.validate_columns(definition, encoded)


def validate_snapshot_in(tx, snapshot_id):
    """현재 트랜잭션의 수정된 현황 전체를 검사하고 저장된 현황을 반환한다.

    현재 관리값과 원본 셀의 값은 달라도 된다. 출처는 각 행 자신의 입력 작업과
    명시된 위치별 입력 작업을 기준으로 확인하며 원본 값·위치는 수정하지 않는다.
    날짜별 HPA·일반 분류는 수정 묶음의 모든 변경을 적용한 최종 상태로 확인한다.
    사업장의 날짜 간 변경 여부는 호출자가 결정하고 이 함수에서는 검사하지 않는다.
    """
    identifier(snapshot_id, "snapshot_id")
    repository = EquipmentRepository()
    row = tx.get("equipment_snapshot", {"snapshot_id": snapshot_id})
    equipment = tx.get("equipment", {"equipment_id": row["equipment_id"]})
    snapshot = repository.load(tx, equipment, row)
    prepared = prepare_snapshot(snapshot)
    groups = {group["group_id"]: group for group in repository.rows(tx, "equipment_group")}
    source_checker = EquipmentService(None)
    batches = {}

    def check_source(value):
        batch_id = value.batch_id
        identifier(batch_id, "batch_id")
        if batch_id not in batches:
            batches[batch_id] = tx.get("import_batch", {"batch_id": batch_id})
        source_checker._check_sources(tx, batches[batch_id], value.source_reference)

    check_source(prepared)
    pm_groups = set()
    for software in prepared.software:
        check_source(software)
        family = tx.get("version_family", {"family_id": software.family_id})
        owner = tx.get("equipment_group", {"group_id": family["group_id"]})
        model_ids = {pm.group_id for pm in software.pms} or {prepared.group_id}
        require(all(model_id in groups for model_id in model_ids),
                "현황에 연결된 모델을 등록 자료에서 확인해야 합니다.")
        require(all(groups[model_id]["category"] == owner["category"] for model_id in model_ids),
                "PM 모델과 버전 계열의 설비군이 다릅니다. KCTC O2와 Oxide O2를 구분해야 합니다.")
        if software.release_version_id is not None:
            release = tx.get("release_version", {"release_version_id": software.release_version_id})
            require(release["family_id"] == software.family_id,
                    "적용 SW와 연결 릴리즈의 버전 계열이 다릅니다.")
        for pm in software.pms:
            check_source(pm)
            pm_groups.add(pm.group_id)

    if pm_groups:
        require(prepared.group_id is None or pm_groups == {prepared.group_id},
                "현황의 대표 모델과 PM별 모델 구성이 다릅니다. PM 구성과 함께 확인해 주세요.")
    excluded = {model_id for model_id, group in groups.items() if not group["analysis_enabled"]}
    read_equipment_record(prepared, excluded)
    snapshot_models = pm_groups | ({prepared.group_id} if prepared.group_id is not None else set())
    source_checker._check_hpa_history(tx, equipment, prepared.base_date, snapshot_models)
    return snapshot


def validate_risk_in(tx, row, context=None):
    """위험 관리값·연결·범위를 검사하고 판정용 RiskRule을 반환한다.

    UNRESOLVED는 릴리즈 교체·삭제 후 대상이 비어 있는 상태를 보존할 수 있다.
    명시된 연결은 같은 계열이어야 하며 VERIFIED는 대상과 확인된 순서가 필요하다.
    """
    require(isinstance(row, Mapping), "검사할 위험 관리값이 필요합니다.")
    values = dict(row)
    encoded = encode_record("risk_rule", values)
    tx.repository.validate_columns(tx.repository.table("risk_rule"), encoded)
    required_text(values.get("risk_reason"), "risk_reason", 65535)
    for field in ("risk_rule_id", "family_id"):
        identifier(values.get(field), field)
    for field in ("group_id", "item_id", "release_version_id",
                  "start_release_version_id", "end_release_version_id"):
        if values.get(field) is not None:
            identifier(values[field], field)

    context = _context_copy(RefreshContext() if context is None else context)
    repository = EquipmentRepository()
    family_rows = repository.rows(tx, "version_family")
    release_rows = repository.rows(tx, "release_version")
    families = _families(family_rows, release_rows, context.aliases)
    family_row = next((family for family in family_rows if family["family_id"] == values["family_id"]), None)
    require(family_row is not None, "위험 정보의 버전 계열이 등록 자료에 없습니다.")
    from setron.features.risk.item_scope import validate_scope_in
    try:
        validate_scope_in(tx, values.get("target_reference"), values["family_id"])
    except ValueError as error:
        require(False, str(error))
    owner = tx.get("equipment_group", {"group_id": family_row["group_id"]})
    if values.get("group_id") is not None:
        group = tx.get("equipment_group", {"group_id": values["group_id"]})
        require(group["category"] == owner["category"],
                "위험 대상 모델과 버전 계열의 설비군이 다릅니다.")

    rule = RiskRule(
        risk_rule_id=values["risk_rule_id"], rule_type=RuleType(values["rule_type"]),
        family_id=values["family_id"], risk_reason=values["risk_reason"],
        group_id=values.get("group_id"), item_id=values.get("item_id"),
        release_version_id=values.get("release_version_id"),
        start_release_version_id=values.get("start_release_version_id"),
        end_release_version_id=values.get("end_release_version_id"),
        is_enabled=bool(values.get("is_enabled", True)),
        mapping_status=MappingStatus(values["mapping_status"]),
    )
    family = families[rule.family_id]
    for field in ("release_version_id", "start_release_version_id", "end_release_version_id"):
        release_id = getattr(rule, field)
        if release_id is not None:
            release = tx.get("release_version", {"release_version_id": release_id})
            require(release["family_id"] == rule.family_id,
                    f"{field}: 위험 정보와 같은 계열의 버전만 연결할 수 있습니다.")
    if rule.item_id is not None:
        tx.get("item", {"item_id": rule.item_id})
        linked = any(
            link["item_id"] == rule.item_id and family.get(link["release_version_id"]) is not None
            for link in repository.rows(tx, "release_version_item")
        )
        if rule.mapping_status is MappingStatus.VERIFIED:
            require(linked, "위험 대상 Item이 해당 계열의 릴리즈에 연결되어 있지 않습니다.")

    if rule.mapping_status is MappingStatus.VERIFIED:
        problems = validate_rule(rule, families)
        require(not problems, " ".join(problems))
        order_counts = Counter(release.version_order for release in family.releases)
        for field in ("start_release_version_id", "end_release_version_id"):
            release_id = getattr(rule, field)
            if release_id is not None:
                release = family.get(release_id)
                require(release.version_order is not None and order_counts[release.version_order] == 1,
                        f"{field}: 시작·해제 버전의 순서를 확인한 뒤 위험 연결을 확정해 주세요.")
    return rule
