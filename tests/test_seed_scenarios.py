"""원본 PM 사례와 승인된 미등록·노후·위험 시나리오를 실제 판정 엔진으로 검사한다."""
from collections import Counter
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sys

import pytest

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.aggregation.service import summarize
from setron.features.aging.schemas import AgeStatus, DEFAULT_THRESHOLDS
from setron.features.aging.service import equipment_age
from setron.features.item.service import ItemCatalog
from setron.features.priority.service import prioritize
from setron.features.risk.schemas import MappingStatus, RiskRule, RiskStatus, RuleType
from setron.features.risk.service import RiskAssessor
from setron.features.version.groups import VersionGroups
from setron.features.version.schemas import CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases

DATABASE = Path(__file__).resolve().parents[1] / "database"
if str(DATABASE) not in sys.path:
    sys.path.insert(0, str(DATABASE))

from build_seed import MODELS, demo_target  # noqa: E402
from seed_augmentation import prepare_seed, snapshot_template  # noqa: E402
from seed_source import candidates, make_plan  # noqa: E402


@pytest.fixture(scope="module")
def data():
    source = json.loads((DATABASE / "demo_release_catalog.json").read_text(encoding="utf-8"))
    before = deepcopy(source)
    catalog, plan = prepare_seed(source)
    assert source == before
    return source, catalog, plan, catalog["seed_augmentation"]["scenarios"]


def software(catalog, entry, observation):
    return observation.get("software", [{"family": snapshot_template(catalog, entry)["family"], **observation}])


@pytest.fixture(scope="module")
def engine(data):
    _, catalog, plan, scenarios = data
    models = {name: i for i, (name, *_rest) in enumerate(MODELS, 1)}
    family_ids = {key: i for i, key in enumerate(catalog["families"], 1)}
    release_id = lambda key, index: family_ids[key] * 10000 + index + 1
    families = {family_ids[key]: FamilyReleases(family_ids[key], [ReleaseVersion(
        release_id(key, index), family_ids[key], version["name"], version["order"], date.fromisoformat(version["date"]))
        for index, version in enumerate(family["versions"])], exact_raw=True)
        for key, family in catalog["families"].items()}
    records, targets = [], {}
    for equipment_id, entry in enumerate(plan, 1):
        template = snapshot_template(catalog, entry)
        model, key = template["model"], template["family"]
        if model != "HPA":
            target = demo_target(catalog, candidates, model, key)
            targets[models[model], family_ids[key]] = VersionGroups.single(release_id(key, target))
        for observation in entry["observations"]:
            pms = []
            for sw in software(catalog, entry, observation):
                for pm in sw.get("pms") or [{"name": None, "model": model}]:
                    pms.append(PmVersion(pm["name"], models[pm["model"]], family_ids[sw["family"]], sw["version"],
                                         release_id(sw["family"], sw["release"]) if sw["release"] is not None else None))
            records.append(EquipmentRecord(equipment_id, entry["code"], date.fromisoformat(observation["date"]),
                template["site"], template["line"], template["model_raw"], tuple(pms), is_hpa=model == "HPA"))
    rules = tuple(RiskRule(index, RuleType.DIRECT_VERSION, family_ids[rule["family"]], rule["risk_reason"],
        group_id=models[rule["model"]], release_version_id=release_id(rule["family"], rule["release"]),
        mapping_status=MappingStatus.VERIFIED) for index, rule in enumerate(scenarios["risk_rules"], 1))
    assessor = RiskAssessor(families, ItemCatalog(families, ()), rules)
    latest = [record for record in records if record.base_date == max(r.base_date for r in records)]
    return models, family_ids, families, records, targets, assessor, latest


def test_source_workbooks_items_releases_and_reproducibility_are_preserved(data):
    source, catalog, plan, _ = data
    assert prepare_seed(source) == (catalog, plan)
    assert catalog["workbooks"][:8] == source["workbooks"]
    for key, family in source["families"].items():
        actual = catalog["families"][key]
        assert actual["items"][:len(family["items"])] == family["items"]
        for original, version in zip(family["versions"], actual["versions"]):
            assert {k: v for k, v in original.items() if k != "items"} == {k: v for k, v in version.items() if k != "items"}
            assert version["items"][:len(original["items"])] == original["items"]


def test_physical_count_model_allocation_lines_and_pm_expansion(data):
    source, catalog, plan, _ = data
    baseline = make_plan(source)
    assert len(plan) == len({entry["code"] for entry in plan}) == 300
    assert sum(len(entry["observations"]) for entry in plan) == 1800
    assert len({(snapshot_template(catalog, entry)["site"], snapshot_template(catalog, entry)["line"]) for entry in plan}) == 25
    assert len(catalog["seed_augmentation"]["registered_lines"]) == 26
    assert Counter(snapshot_template(catalog, entry)["model"] for entry in plan) == Counter(
        source["templates"][entry["template"]]["model"] for entry in baseline)
    sw = [sw for entry in plan for observation in entry["observations"] for sw in software(catalog, entry, observation)]
    assert len(sw) == 1807
    assert sum(len(version.get("pms", [])) for version in sw) == 36
    for entry in plan:
        for observation in entry["observations"]:
            values = software(catalog, entry, observation)
            assert len({(sw["family"], sw["version"]) for sw in values}) == len(values)
            names = [pm["name"] for sw in values for pm in sw.get("pms", [])]
            assert len(names) == len(set(names))


def test_original_mixed_equipment_keeps_original_site_line_pm_assignment_and_sources(data, engine):
    source, catalog, plan, scenarios = data
    models, _, _, _, _, _, latest = engine
    entry = next(entry for entry in plan if entry["code"] == "ESON81")
    template = snapshot_template(catalog, entry)
    assert (template["site"], template["line"], template["area"], template["model_raw"], template["group_model"]) == (
        "화성", "NRD (MR2동)", "FOUNDRY", "M-O2/O3", None)
    observed = entry["observations"][-1]["software"]
    assert [(sw["family"], sw["version"], [pm["name"] for pm in sw["pms"]]) for sw in observed] == [
        ("o3", "v4.50.1.1_a", ["PM1", "PM2"]), ("o2_foundry", "v3.53_F2", ["PM3", "PM4"])]
    assert all(sw["sheet"] == "Foundry" and sw["cell"] == "G57" for sw in observed)
    for sw in observed:
        original = source["workbooks"][sw["workbook"]]["sheets"]["Foundry"]
        assert sw["version"] in original["G57"]
        assert all(pm["source_reference"]["pm_name"]["cells"] == ["P57"] for pm in sw["pms"])
        assert all(pm["model_raw"] in original["P57"] for pm in sw["pms"])
    record = next(record for record in latest if record.equipment_code == "ESON81")
    assert [(pm.pm_name, pm.group_id) for pm in record.pms] == [("PM1", models["M-O3"]), ("PM2", models["M-O3"]),
                                                              ("PM3", models["M-O2"]), ("PM4", models["M-O2"])]
    assert scenarios["mixed_pm"]["historical_inference"]


def test_known_releases_do_not_predate_their_observations(data):
    _, catalog, plan, _ = data
    for entry in plan:
        for observation in entry["observations"]:
            for sw in software(catalog, entry, observation):
                if sw["release"] is None:
                    continue
                version = catalog["families"][sw["family"]]["versions"][sw["release"]]
                assert version["name"] == sw["version"]
                assert version["date"] <= observation["date"]


def test_three_unregistered_versions_remain_unknown_without_implicit_alias(data, engine):
    _, catalog, plan, scenarios = data
    _, _, families, _, targets, _, latest = engine
    assert len(scenarios["unregistered"]) == 3
    for scenario in scenarios["unregistered"]:
        entry = next(entry for entry in plan if entry["code"] == scenario["equipment_code"])
        observation = entry["observations"][-1]
        assert observation["release"] is None and observation["version"] == scenario["version"]
        assert scenario["version"].strip().casefold() not in {
            version["name"].strip().casefold() for version in catalog["families"][scenario["family"]]["versions"]}
        assert catalog["workbooks"][observation["workbook"]]["sheets"][observation["sheet"]][observation["cell"]] == observation["version"]
    summary = summarize(latest, families, targets)
    assert summary.equipment_count == 285 and summary.hpa_excluded == 15
    assert summary.undetermined == 3
    assert sum(model.equipment_count for model in summary.by_model) == 286  # 혼합 1대는 O2와 O3에 각각 포함.


def test_pm_mismatch_is_visible_and_history_does_not_downgrade(data, engine):
    _, catalog, plan, scenarios = data
    _, family_ids, families, _, targets, _, latest = engine
    case = scenarios["pm_mismatch"]
    entry = next(entry for entry in plan if entry["code"] == case["equipment_code"])
    orders = {}
    for observation in entry["observations"]:
        for sw in observation["software"]:
            order = catalog["families"][sw["family"]]["versions"][sw["release"]]["order"]
            for pm in sw["pms"]:
                assert order >= orders.get(pm["name"], order)
                orders[pm["name"]] = order
    status = next(status for status in summarize(latest, families, targets).equipment if status.record.equipment_code == case["equipment_code"])
    assert len(status.families) == 1 and status.families[0].family_id == family_ids["c4"]
    assert status.families[0].pm_mismatch and status.families[0].status is CompareStatus.BELOW


def test_aged_devices_produce_warning_and_danger_with_separate_natural_item_text(data, engine):
    source, catalog, _, scenarios = data
    _, _, families, _, _, _, latest = engine
    assert {scenario["expected_status"] for scenario in scenarios["aging"]} == {"WARNING", "DANGER"}
    contents = []
    for scenario in scenarios["aging"]:
        record = next(record for record in latest if record.equipment_code == scenario["equipment_code"])
        assert equipment_age(record, families, DEFAULT_THRESHOLDS).status is AgeStatus(scenario["expected_status"])
        family = catalog["families"][scenario["family"]]
        version = next(version for version in family["versions"] if version["name"] == scenario["version"])
        item = family["items"][version["items"][0]]
        assert item["synthetic"] and item["inference"]["not_product_release_or_defect"]
        assert item["content"] not in {original["content"] for original in source["families"][scenario["family"]]["items"]}
        contents.append(item["content"])
    assert len(contents) == len(set(contents)) == 2


def test_direct_version_risk_and_priority_branches_are_active(data, engine):
    _, _, _, scenarios = data
    _, _, families, _, targets, assessor, latest = engine
    cases = {scenario["equipment_code"] for scenario in scenarios["aging"]}
    findings = {record.equipment_code: assessor.equipment_risk(record).status for record in latest if not record.is_hpa}
    assert {code for code, status in findings.items() if status is RiskStatus.RISK} == cases
    priority = prioritize(latest, assessor, families, DEFAULT_THRESHOLDS, targets=targets)
    assert len(priority.ranked) == 282 and len(priority.needs_check) == 3
    assert {entry.record.equipment_code for entry in priority.ranked[:2]} == cases
    for rule in scenarios["risk_rules"]:
        reference = rule["target_reference"]
        assert rule["rule_type"] == "DIRECT_VERSION" and reference["synthetic"] and reference["not_product_defect"]
        assert reference["source"]["workbook"] == scenarios["workbook"]


def test_scenario_display_fields_avoid_mock_labels_and_hpa_area_is_unknown(data):
    _, catalog, plan, scenarios = data
    for entry in plan:
        assert "DEMO" not in entry["code"]
        for observation in entry["observations"]:
            assert all("DEMO" not in (sw["version"] or "") for sw in software(catalog, entry, observation))
    assert all(snapshot_template(catalog, entry)["area"] is None for entry in plan
               if snapshot_template(catalog, entry)["model"] == "HPA")
    book = catalog["workbooks"][scenarios["workbook"]]
    assert book["synthetic"] and len(book["sha256"]) == 64 and book["notice"]
