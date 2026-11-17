"""선택 릴리즈 미만·동일·초과의 엄격한 위험 판정과 저장/재연결을 검증한다."""

from dataclasses import replace
from datetime import date

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.db_import.dump import DUMP_COLUMNS, project_row
from setron.features.db_import.references import rewrite_json
from setron.features.item.service import ItemCatalog
from setron.features.management import ManagementService, RecordEdit
from setron.features.release.replacement import FamilyReplacementService
from setron.features.risk.schemas import FindingBasis, MappingStatus, RiskKind, RiskRule, RiskStatus, RuleType
from setron.features.risk.service import RiskAssessor, validate_rule
from setron.features.storage.validation import decode_record, encode_record
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.database_source import load_dashboard_source

from test_release_replacement import original, request, reviewed
from test_release_revision import all_rows, observation, release, revision_storage  # noqa: F401


def families():
    return {
        10: FamilyReleases(10, (
            ReleaseVersion(101, 10, "v999", 1), ReleaseVersion(102, 10, "v2", 2),
            ReleaseVersion(103, 10, "v1", 3), ReleaseVersion(104, 10, "순서 미확인", None),
            ReleaseVersion(105, 10, "중복 A", 4), ReleaseVersion(106, 10, "중복 B", 4),
        )),
        20: FamilyReleases(20, (ReleaseVersion(201, 20, "다른 계열", 1),)),
    }


def rule(kind, **changes):
    return replace(RiskRule(1, kind, 10, "검증용 버전 조건", release_version_id=102,
                            mapping_status=MappingStatus.VERIFIED), **changes)


def pm(raw, *, model=1, release_id=None):
    return PmVersion("PM1", model, 10, raw, release_id)


def record(*pms, **changes):
    return replace(EquipmentRecord(1, "EQ-1", date(2026, 10, 1), "화성", "P1", "모델", tuple(pms)), **changes)


def assessor(value):
    known = families()
    return RiskAssessor(known, ItemCatalog(known, ()), (value,))


@pytest.mark.parametrize("kind,raw,expected", [
    (RuleType.VERSION_BELOW, "v999", RiskStatus.RISK),
    (RuleType.VERSION_BELOW, "v2", RiskStatus.NO_RISK),
    (RuleType.VERSION_BELOW, "v1", RiskStatus.NO_RISK),
    (RuleType.DIRECT_VERSION, "v999", RiskStatus.NO_RISK),
    (RuleType.DIRECT_VERSION, "v2", RiskStatus.RISK),
    (RuleType.DIRECT_VERSION, "v1", RiskStatus.NO_RISK),
    (RuleType.VERSION_ABOVE, "v999", RiskStatus.NO_RISK),
    (RuleType.VERSION_ABOVE, "v2", RiskStatus.NO_RISK),
    (RuleType.VERSION_ABOVE, "v1", RiskStatus.RISK),
])
def test_relations_use_confirmed_release_order_and_exclude_equal_boundary(kind, raw, expected):
    risk = assessor(rule(kind))
    result = risk.equipment_risk(record(pm(raw)))
    assert result.status is expected
    release_result = risk.release_findings(families()[10].match(raw))
    assert bool(release_result) == (expected is RiskStatus.RISK)
    if result.findings:
        assert result.findings[0].basis.value == kind.value
        assert result.findings[0].kind is RiskKind.APPLIED


@pytest.mark.parametrize("kind", [RuleType.VERSION_BELOW, RuleType.VERSION_ABOVE])
@pytest.mark.parametrize("raw,reference,basis", [
    (None, 102, FindingBasis.VERSION_UNKNOWN),
    ("미등록", 102, FindingBasis.VERSION_UNKNOWN),
    ("순서 미확인", 102, FindingBasis.VERSION_ORDER_UNKNOWN),
    ("중복 A", 102, FindingBasis.VERSION_ORDER_UNKNOWN),
    ("v999", 104, FindingBasis.VERSION_ORDER_UNKNOWN),
    ("v999", 105, FindingBasis.VERSION_ORDER_UNKNOWN),
    ("v999", None, FindingBasis.TARGET_INCOMPLETE),
    ("v999", 201, FindingBasis.TARGET_INCOMPLETE),
])
def test_missing_unknown_and_duplicated_versions_remain_unknown(kind, raw, reference, basis):
    result = assessor(rule(kind, release_version_id=reference)).equipment_risk(record(pm(raw)))
    assert result.status is RiskStatus.UNKNOWN and result.findings[0].basis is basis


def test_exact_identity_keeps_legacy_equality_semantics_without_order():
    value = rule(RuleType.DIRECT_VERSION, release_version_id=104)
    assert validate_rule(value, families()) == ()
    assert assessor(value).equipment_risk(record(pm("순서 미확인"))).status is RiskStatus.RISK


@pytest.mark.parametrize("kind", [RuleType.VERSION_BELOW, RuleType.VERSION_ABOVE])
def test_directional_reference_requires_known_unique_order_and_same_family(kind):
    assert validate_rule(rule(kind), families()) == ()  # Item 연결 없이도 버전 기준을 등록한다.
    for reference in (104, 105):
        assert any("순서" in problem for problem in validate_rule(rule(kind, release_version_id=reference), families()))
    assert any("같은 계열" in problem for problem in validate_rule(rule(kind, release_version_id=201), families()))


def test_mixed_pm_unknown_hpa_and_explicit_model_site_line_scope():
    value = rule(RuleType.VERSION_BELOW, target_model_ids=(1,), target_site="화성", target_line="P1")
    risk = assessor(value)
    assert risk.equipment_risk(record(pm("v999"), pm("v2"))).status is RiskStatus.RISK
    assert risk.equipment_risk(record(pm("순서 미확인"), pm("v2"))).status is RiskStatus.UNKNOWN
    assert risk.equipment_risk(record(pm("순서 미확인"), pm("v999"))).status is RiskStatus.RISK
    assert risk.equipment_risk(record(pm("v999", model=2))).status is RiskStatus.NO_RISK
    assert risk.equipment_risk(record(pm("v999"), site="평택")).status is RiskStatus.NO_RISK
    assert risk.equipment_risk(record(pm("v999"), line="P2")).status is RiskStatus.NO_RISK
    assert risk.pm_findings(pm("v999"))[0].basis is FindingBasis.SCOPE_UNKNOWN
    summary = risk.summarize((record(pm("v999")), record(pm("v999"), equipment_id=2, is_hpa=True)))
    assert summary.hpa_excluded == 1 and summary.counts()[RiskStatus.RISK] == 1


def values(kind, reference, **changes):
    return {"rule_type": kind, "family_id": 1, "release_version_id": reference,
            "risk_reason": "선택 기준 버전과 비교", "mapping_status": "VERIFIED", **changes}


@pytest.mark.parametrize("kind,risky_code", [
    ("VERSION_BELOW", "LOW"), ("DIRECT_VERSION", "EQUAL"), ("VERSION_ABOVE", "HIGH"),
])
def test_create_preview_readback_and_dump_projection_preserve_relation(revision_storage, kind, risky_code):
    storage = revision_storage
    ids = [release(storage, raw=raw, order=order) for raw, order in (("v999", 1), ("v2", 2), ("v1", 3))]
    for code, raw, release_id in zip(("LOW", "EQUAL", "HIGH"), ("v999", "v2", "v1"), ids):
        observation(storage, code=code, versions=((1, 1, raw, release_id),))
    payload = values(kind, ids[1], item_manager={
        "schema": 1, "name": "조건 검증", "category": "공정 위험", "memo": "원문 보존",
        "model_ids": [1], "site": "화성", "line": "Line1",
    })
    service = ManagementService(storage)
    before = {name: all_rows(storage, name) for name in ("risk_rule", "import_batch")}
    preview = service.preview_risk_rule(payload)
    assert preview.saveable and [entry.record.equipment_code for entry in preview.equipment] == [risky_code]
    assert {name: all_rows(storage, name) for name in before} == before
    saved = service.create_risk_rule(payload).records[0].current
    assert saved["rule_type"] == kind and saved["release_version_id"] == ids[1] and saved["item_id"] is None
    assert saved["target_reference"]["direct_version"]["version_raw"] == "v2"
    assert saved["target_reference"]["item_manager"]["model_ids"] == [1]
    source = load_dashboard_source(storage, publish_lines=False)
    equipment = {entry["equipment_id"]: entry["equipment_code"] for entry in all_rows(storage, "equipment")}
    assert {equipment[eid] for eid, status in source.risks.items() if status is RiskStatus.RISK} == {risky_code}
    restored = source.assessor.rules[0]
    assert restored.rule_type.value == kind and restored.mapping_status is MappingStatus.VERIFIED
    assert restored.target_model_ids == (1,) and restored.target_line == "Line1"
    encoded = encode_record("risk_rule", saved)
    projected = project_row("risk_rule", encoded, DUMP_COLUMNS["risk_rule"])
    identity = lambda _table, value: value
    remapped = rewrite_json("risk_rule", projected, identity, ids_changed=False)
    assert decode_record("risk_rule", remapped) == saved


@pytest.mark.parametrize("kind", ["VERSION_BELOW", "VERSION_ABOVE"])
@pytest.mark.parametrize("invalid", ["unordered", "duplicated", "other-family", "missing"])
def test_invalid_directional_reference_is_rejected_without_saving(revision_storage, kind, invalid):
    storage = revision_storage
    first = release(storage, raw="v1", order=1)
    selected = release(storage, raw="reference", order=None if invalid == "unordered" else 2,
                       family=2 if invalid == "other-family" else 1)
    if invalid == "duplicated":
        with storage.transaction() as tx:
            tx.update("release_version", {"release_version_id": first}, {"version_order": 2})
    before = all_rows(storage, "risk_rule")
    service = ManagementService(storage)
    payload = values(kind, None if invalid == "missing" else selected)
    assert not service.preview_risk_rule(payload).saveable
    with pytest.raises(ValueError):
        service.create_risk_rule(payload)
    assert all_rows(storage, "risk_rule") == before


@pytest.mark.parametrize("invalid", ["unordered", "duplicated"])
def test_directional_rule_becomes_unresolved_if_reference_order_changes(revision_storage, invalid):
    storage = revision_storage
    low, selected = release(storage, raw="v1", order=1), release(storage, raw="v2", order=2)
    observation(storage, versions=((1, 1, "v1", low),))
    ManagementService(storage).create_risk_rule(values("VERSION_BELOW", selected))
    with storage.transaction() as tx:
        tx.update("release_version", {"release_version_id": selected},
                  {"version_order": None if invalid == "unordered" else 1})
    source = load_dashboard_source(storage, publish_lines=False)
    assert source.assessor.rules[0].mapping_status is MappingStatus.UNRESOLVED
    assert set(source.risks.values()) == {RiskStatus.UNKNOWN}


def test_management_edit_switches_strict_relation_and_recalculates_saved_risk(revision_storage):
    storage = revision_storage
    low, selected, high = (release(storage, raw=raw, order=order)
                           for raw, order in (("low", 1), ("reference", 2), ("high", 3)))
    for code, raw, release_id in (("LOW", "low", low), ("HIGH", "high", high)):
        observation(storage, code=code, versions=((1, 1, raw, release_id),))
    service = ManagementService(storage)
    created = service.create_risk_rule(values("VERSION_BELOW", selected)).records[0]
    result = service.save((RecordEdit("risk_rule", created.key, created.revision,
                                      {"rule_type": "VERSION_ABOVE"}),))
    assert result.records[0].current["rule_type"] == "VERSION_ABOVE"
    assert result.records[0].current["release_version_id"] == selected
    source = load_dashboard_source(storage, publish_lines=False)
    equipment = {entry["equipment_id"]: entry["equipment_code"] for entry in all_rows(storage, "equipment")}
    assert {equipment[eid] for eid, status in source.risks.items() if status is RiskStatus.RISK} == {"HIGH"}


@pytest.mark.parametrize("kind", ["VERSION_BELOW", "VERSION_ABOVE"])
def test_family_replacement_preserves_version_condition_without_requiring_item(revision_storage, kind):
    storage = revision_storage
    initial = original(storage)
    selected = initial.release.current["release_version_id"]
    ManagementService(storage).create_risk_rule(values(kind, selected))
    service, incoming = FamilyReplacementService(storage), request()
    proposal = service.preview(incoming).risk_reconnections[0]
    assert proposal.can_restore and proposal.proposed_targets["release_version_id"] is not None
    service.replace(incoming, reviewed(service, incoming))
    saved = all_rows(storage, "risk_rule")[0]
    assert saved["rule_type"] == kind and saved["release_version_id"] == selected
    assert saved["item_id"] is None and saved["mapping_status"] == "VERIFIED"
    unknown_order = request(order=None)
    proposal = service.preview(unknown_order).risk_reconnections[0]
    assert not proposal.can_restore and "순서" in proposal.reason
