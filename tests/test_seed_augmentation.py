"""실제 시드 계획의 누락 보완·원본 보존·날짜·출처·모델 적용 근거를 함께 확인한다."""

from collections import Counter
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sys

import pytest

from setron.features.version.schemas import CompareStatus, ReleaseVersion
from setron.features.version.service import FamilyReleases, compare_version

DATABASE = Path(__file__).resolve().parents[1] / "database"
if str(DATABASE) not in sys.path:
    sys.path.insert(0, str(DATABASE))

from build_seed import demo_target  # noqa: E402
from seed_augmentation import HPA_VERSION, RELATED_FAMILIES, REGISTERED_LINES_KEY, prepare_seed, snapshot_template  # noqa: E402
from seed_source import candidates, make_plan  # noqa: E402


@pytest.fixture(scope="module")
def dataset():
    source = json.loads((DATABASE / "demo_release_catalog.json").read_text(encoding="utf-8"))
    effective, plan = prepare_seed(source)
    return source, effective, plan


def test_augmentation_preserves_source_and_is_reproducible(dataset):
    source, effective, plan = dataset
    before = deepcopy(source)
    assert prepare_seed(source) == (effective, plan)
    assert source == before
    assert effective["workbooks"][:len(source["workbooks"])] == source["workbooks"]
    assert effective["templates"][:len(source["templates"])] == source["templates"]
    for key, family in source["families"].items():
        assert effective["families"][key]["items"][:len(family["items"])] == family["items"]
        for old, new in zip(family["versions"], effective["families"][key]["versions"]):
            assert {k: v for k, v in new.items() if k != "items"} == {k: v for k, v in old.items() if k != "items"}
            assert new["items"][:len(old["items"])] == old["items"]


def test_every_observation_has_exact_release_without_future_dates(dataset):
    source, effective, plan = dataset
    baseline = make_plan(source)
    assert len(plan) == 300 and sum(len(entry["observations"]) for entry in plan) == 1800
    areas = Counter(snapshot_template(effective, entry)["area"] for entry in plan)
    assert areas == {"MEMORY": 205, "FOUNDRY": 80, None: 15}
    adjustments = {(row["equipment_code"], row["base_date"]): row
                   for row in effective["seed_augmentation"]["history_adjustments"]}
    assignments = effective["seed_augmentation"]["equipment_assignments"]
    scenarios = effective["seed_augmentation"]["scenarios"]
    aging = {row["equipment_code"]: row for row in scenarios["aging"]}
    unregistered = {(row["equipment_code"], row["date"]): row for row in scenarios["unregistered"]}
    hpa_count = 0
    for number, (old_entry, entry) in enumerate(zip(baseline, plan)):
        assert entry["code"] == assignments[number]["equipment_code"]
        template = effective["templates"][entry["template"]]
        old_template = source["templates"][old_entry["template"]]
        assert tuple(template[field] for field in ("model", "family", "site", "area")) == tuple(
            old_template[field] for field in ("model", "family", "site", "area"))
        for old, observation in zip(old_entry["observations"], entry["observations"]):
            assert observation["date"] == old["date"]
            adjustment = adjustments.get((entry["code"], observation["date"]))
            if entry["code"] in aging:
                assert observation["version"] == aging[entry["code"]]["version"]
            elif (entry["code"], observation["date"]) in unregistered:
                assert observation["version"] == unregistered[entry["code"], observation["date"]]["version"]
                assert observation["release"] is None
            elif adjustment:
                assert adjustment["previous_version"] == old["version"]
                assert adjustment["version"] == observation["version"]
                assert observation["date"] < adjustment["known_release_date"]
            elif old["version"]:
                assert observation["version"] == old["version"]
                assert (observation["workbook"], observation["sheet"], observation["cell"]) == (old["workbook"], old["sheet"], old["cell"])
            if observation["release"] is None:
                assert (entry["code"], observation["date"]) in unregistered
                continue
            version = effective["families"][template["family"]]["versions"][observation["release"]]
            assert observation["version"] == version["name"]
            assert date.fromisoformat(version["date"]) <= date.fromisoformat(observation["date"])
            if template["model"] == "HPA":
                hpa_count += 1
                assert version["synthetic"] and observation["version"] == HPA_VERSION
    assert hpa_count == 90
    assert len(adjustments) == 56
    assert len({code for code, day in adjustments}) == 25


def test_synthetic_fields_resolve_to_labeled_separate_source(dataset):
    source, effective, _ = dataset
    book_id = len(source["workbooks"])
    synthetic_book = effective["workbooks"][book_id]
    assert synthetic_book["synthetic"] and "합성" in synthetic_book["notice"]
    for family in effective["families"].values():
        for item in family["items"]:
            if item.get("synthetic") and item.get("workbook") == book_id:
                assert item["workbook"] == book_id
                cells = synthetic_book["sheets"][item["sheet"]]
                assert cells[item["cell"]] == item["content"]
                assert "[합성 시연]" not in item["content"] and "조회·버전 비교" not in item["content"]
                basis = item["inference"]
                assert basis["model_applicability_inferred"]
                donor = source["workbooks"][basis["workbook"]]["sheets"][basis["sheet"]][basis["cell"]]
                assert donor and donor != item["content"]
                assert cells[item["model_cell"]] == ", ".join(item["models"])
        for version in family["versions"]:
            if version.get("synthetic") and version.get("workbook") == book_id:
                assert version["workbook"] == book_id
                cells = synthetic_book["sheets"][version["sheet"]]
                assert cells[version["cell"]] == version["name"]
                assert cells[version["date_cell"]] == version["date"]
                assert cells[version["order_cell"]] == version["order"]
                assert version["items"]
                assert "DEMO" not in version["name"]


def test_every_non_hpa_pair_has_valid_n_and_comparable_installed_versions(dataset):
    _, effective, plan = dataset
    families = {}
    for family_id, (key, family) in enumerate(effective["families"].items(), 1):
        families[key] = FamilyReleases(family_id, [ReleaseVersion(index + 1, family_id, version["name"],
            version["order"], date.fromisoformat(version["date"])) for index, version in enumerate(family["versions"])], exact_raw=True)
        assert not families[key].sorted.duplicated_orders
    targets = {}
    for entry in plan:
        template = effective["templates"][entry["template"]]
        key, model = template["family"], template["model"]
        if model == "HPA":
            continue
        target = demo_target(effective, candidates, model, key)
        assert target is not None
        targets[model, key] = target
        applicable = {index for index, _ in candidates(effective, model, key)}
        for observation in entry["observations"]:
            if observation["release"] is None:
                assert any(row["equipment_code"] == entry["code"] and row["version"] == observation["version"]
                           for row in effective["seed_augmentation"]["scenarios"]["unregistered"])
                continue
            assert observation["release"] in applicable
            comparison = compare_version(families[key], observation["version"], target + 1,
                                         release_version_id=observation["release"] + 1)
            assert comparison.status is not CompareStatus.UNDETERMINED
    assert len(targets) == 18 and ("M-O5 Prime", "o5") in targets


def test_all_source_lines_are_covered_with_approved_p4_split_and_no_quantity_change(dataset):
    source, effective, plan = dataset
    values = [snapshot_template(effective, entry) for entry in plan]
    counts = Counter((value["site"], value["line"]) for value in values)
    source_lines = {(template["site"], template["line"]) for template in source["templates"]}
    assert len(source_lines) == 24
    assert set(counts) == (source_lines - {("평택", "P4F")}) | {("평택", "P4H"), ("평택", "P4D")}
    assert len(counts) == 25
    assert counts["평택", "P4H"] == 10 and counts["평택", "P4D"] == 9
    assert counts["화성", "M1"] >= 1 and counts["화성", "NRD-V (V1)"] >= 1
    baseline = make_plan(source)
    assert Counter((value["model"], value["family"]) for value in values) == Counter(
        (source["templates"][entry["template"]]["model"], source["templates"][entry["template"]]["family"])
        for entry in baseline)
    replacements = effective["seed_augmentation"]["source_line_coverage_replacements"]
    assert {row["selected_line"] for row in replacements} == {"M1", "NRD-V (V1)"}


def test_p4_split_preserves_original_fields_and_points_line_to_synthetic_cells(dataset):
    source, effective, plan = dataset
    book_id = len(source["workbooks"])
    changed = [entry for entry in plan if entry.get("snapshot_overrides", {}).get("line") in {"P4H", "P4D"}]
    assert len(changed) == 19
    for entry in changed:
        original = source["templates"][entry["template"]]
        value = snapshot_template(effective, entry)
        assert original["site"] == value["site"] == "평택"
        assert original["line"] == "P4F" and value["line"] in {"P4H", "P4D"}
        assert value["area"] == "MEMORY"
        assert {key: val for key, val in value.items() if key not in {"line", "area"}} == {
            key: val for key, val in original.items() if key not in {"line", "area"}}
        reference = entry["snapshot_sources"]["line"]
        assert reference["workbook"] == book_id
        cells = effective["workbooks"][book_id]["sheets"][reference["sheet"]]
        address = reference["cells"][0]
        row = address[1:]
        assert cells[address] == value["line"] and cells[f"C{row}"] == "P4F"
        assert cells[f"A{row}"] == entry["code"] and cells[f"G{row}"].startswith("[합성]")
        assert len(entry["observations"]) == 6


def test_registered_line_setting_covers_all_effective_lines_and_classifies_them(dataset, monkeypatch):
    from setron.features import reference_data
    from setron.features.data_admin.service import registered_lines

    _, effective, plan = dataset
    registered = effective["seed_augmentation"]["registered_lines"]
    assert len(registered) == 26
    expected = {(value["site"], value["line"], value["area"])
                for value in (snapshot_template(effective, entry) for entry in plan) if value["area"] is not None}
    class SettingRows:
        def get(self, table, key):
            assert table == "app_setting"
            if key != {"setting_key": REGISTERED_LINES_KEY}:
                raise LookupError(key)
            return {"setting_value": {"lines": registered}}
    actual = registered_lines(SettingRows())
    assert {(row["site"], row["line"], row["fab"]) for row in actual} == expected
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", {})
    reference_data.use_registered_lines(actual)
    for site, line, area in expected:
        # 한 장소의 두 생산 구분을 가진 NRD는 각 설비의 명시 구분을 보존한다.
        areas = {value for candidate_site, candidate_line, value in expected if (candidate_site, candidate_line) == (site, line)}
        if len(areas) == 1:
            assert reference_data.default_area(site, line) == area


def test_line_registry_is_emitted_and_existing_registry_blocks_installation(dataset):
    from build_seed import build_sql, literal

    source, _, _ = dataset
    sql = build_sql(source)
    install = next(statement for statement in sql.split(";\n\n") if statement.startswith("SET @seed_install"))
    assert "setting_key = " + literal(REGISTERED_LINES_KEY) in install
    inserts = [statement for statement in sql.split(";\n\n") if statement.startswith("INSERT INTO app_setting")
               and "SELECT " + literal(REGISTERED_LINES_KEY) + "," in statement]
    assert len(inserts) == 1
    # 19대의 6개 기준일 모두에 합성 Line 출처의 입력 작업 ID가 별도로 포함되어야 한다.
    snapshots = [statement for statement in sql.split(";\n\n") if statement.startswith("INSERT INTO equipment_snapshot")]
    line_refs = [statement for statement in snapshots if "CONCAT(" in statement and "@batch_8" in statement]
    assert len(line_refs) == 19 * 6 and all("@batch_8" in statement for statement in line_refs)


def test_natural_codes_and_content_keep_explicit_inference_metadata(dataset):
    source, effective, plan = dataset
    meta = effective["seed_augmentation"]
    assert len({entry["code"] for entry in plan}) == 300
    assert all(not entry["code"].startswith("DEMO-") for entry in plan)
    assert sum(row["source_kind"] == "ORIGINAL_EXCEL" for row in meta["equipment_assignments"]) >= 278
    assert {row["source_kind"] for row in meta["equipment_assignments"]} == {"ORIGINAL_EXCEL", "PATTERN_AUGMENTED"}
    assert len(meta["inferred_releases"]) == 32
    assert len(meta["inferred_items"]) == 37
    for key, family in effective["families"].items():
        contents = [item["content"] for item in family["items"] if item.get("synthetic")]
        assert len(contents) == len(set(contents)), key
        assert not set(contents) & {item["content"] for item in source["families"].get(key, {}).get("items", [])}
    assert any(row["family"] == "hpa" and "원본 없음" in row["basis"]["kind"] for row in meta["inferred_releases"])
    assert len(meta["original_equipment_conflicts"]) == 1


def test_related_known_dates_are_never_backdated_and_histories_do_not_downgrade(dataset):
    source, effective, plan = dataset
    for key, family in effective["families"].items():
        related = source["families"].get(key if key in source["families"] else RELATED_FAMILIES.get(key), {})
        for version in family["versions"]:
            if not version.get("synthetic"):
                continue
            matching_dates = [v["date"] for v in related.get("versions", [])
                              if v["name"].strip().casefold() == version["name"].strip().casefold()]
            assert not matching_dates or version["date"] >= max(matching_dates)
    for entry in plan:
        template = effective["templates"][entry["template"]]
        versions = effective["families"][template["family"]]["versions"]
        orders = [versions[observation["release"]]["order"] for observation in entry["observations"]
                  if observation["release"] is not None]
        assert orders == sorted(orders), entry["code"]


def test_hpa_area_is_not_applicable_with_exact_original_note(dataset):
    source, effective, plan = dataset
    evidence = effective["seed_augmentation"]["hpa_production_area"]
    assert len(evidence) == 15
    assert all(row["production_area"] is None and row["classification"] == "NOT_APPLICABLE" for row in evidence)
    for row in evidence:
        assert row["reason"] == source["workbooks"][row["workbook"]]["sheets"]["S5"]["Z20"]
    assert all(snapshot_template(effective, entry)["area"] is None for entry in plan
               if effective["templates"][entry["template"]]["model"] == "HPA")


def test_pm_and_approved_decision_branches_are_emitted_with_exact_row_counts(dataset):
    from build_seed import build_sql
    from test_demo_seed_targets import _split
    import re

    source, effective, _ = dataset
    sql = build_sql(source)
    statements = sql.split(";\n\n")
    expected = {"equipment": 300, "equipment_snapshot": 1800, "equipment_version": 1807,
                "equipment_pm": 36, "risk_rule": 2}
    for table, count in expected.items():
        assert sum(statement.startswith(f"INSERT INTO {table} (") for statement in statements) == count
    jira = []
    item_statements = [statement for statement in statements if statement.startswith("INSERT INTO item (")]
    assert len(item_statements) == 2398
    for statement in item_statements:
        heading, body = statement.split("\nSELECT ", 1)
        columns = heading[len("INSERT INTO item ("):-1].split(", ")
        assert "jira_code" in columns and "jira_key" not in columns
        for column in columns:
            expression, body = _split(body)
            if column == "jira_code":
                if expression != "NULL":
                    value = re.fullmatch(r"CONVERT\(0x([0-9a-f]+) USING utf8mb4\) COLLATE utf8mb4_unicode_ci", expression)
                    assert value
                    jira.append(bytes.fromhex(value[1]).decode("utf-8"))
                break
    expected_jira = [item["jira_key"] for family in effective["seed_augmentation"]["source_item_jira"].values()
                     for item in family.values()]
    assert Counter(jira) == Counter(expected_jira) and len(jira) == 987 and len(set(jira)) == 945
    scenarios = effective["seed_augmentation"]["scenarios"]
    assert len(scenarios["unregistered"]) == 3 and len(scenarios["aging"]) == 2
    assert len(scenarios["risk_rules"]) == 2
    assert all(rule["target_reference"]["synthetic"] for rule in scenarios["risk_rules"])


def test_new_seed_key_and_actual_code_collisions_prevent_old_dataset_skip(dataset):
    from build_seed import build_sql, literal
    from seed_source import GENERATOR_REVISION, SEED_KEY

    source, _, plan = dataset
    sql = build_sql(source)
    assert SEED_KEY.startswith("demo_seed.v4:") and GENERATOR_REVISION == 9
    installed = next(statement for statement in sql.split(";\n\n") if statement.startswith("SET @seed_done"))
    assert literal(SEED_KEY) in installed and literal("demo_seed.v2:excel:300x6:2026-10-21") not in installed
    assert literal("demo_seed.v3:excel:300x6:2026-10-21") not in installed
    collision = next(statement for statement in sql.split(";\n\n") if statement.startswith("SET @seed_install"))
    assert "equipment_code IN (" in collision and "equipment_code BETWEEN" not in collision
    assert all(literal(entry["code"]) in collision for entry in plan)


def test_original_jira_coverage_and_precise_source_cells(dataset):
    from setron.features.item.jira import extract_jira_key

    source, effective, _ = dataset
    meta = effective["seed_augmentation"]
    assert meta["jira_coverage"]["source_items"] == 2359
    assert meta["jira_coverage"]["with_jira"] == 987
    assert meta["jira_coverage"]["unique_jira"] == 945
    evidence = meta["source_item_jira"]
    assert {key: len(values) for key, values in evidence.items()} == {
        "c3_legacy": 65, "c3": 87, "c3_foundry": 38, "c4": 171,
        "o2": 83, "o2_foundry": 36, "o5": 507}
    for key, values in evidence.items():
        family = source["families"][key]
        raw = source["workbooks"][family["workbook"]]["sheets"]
        for index, value in values.items():
            assert int(index) < len(family["items"])
            refs = value["source_reference"]["jira_key"]
            refs = refs if isinstance(refs, list) else [refs]
            codes = {extract_jira_key(raw[location["sheet"]][cell]) for location in refs for cell in location["cells"]}
            assert codes == {value["jira_key"]}
    for key, content_cell, code, jira_cell in (("c3_legacy", "D4", "ETOJ-3703", "E4"),
            ("c4", "D4", "ETOJ-4120", "E4"), ("o5", "E4", "ETOJ-6532", "D4"),
            ("o2", "B6", "ETOJ-3257", "B6"), ("o2_foundry", "B6", "ETOJ-7551", "B6")):
        index = next(i for i, item in enumerate(source["families"][key]["items"]) if item["cell"] == content_cell)
        value = evidence[key][str(index)]
        assert value["jira_key"] == code
        assert value["source_reference"]["jira_key"]["cells"] == [jira_cell]


def test_inferred_items_do_not_copy_original_ticket_numbers(dataset):
    from setron.features.item.jira import extract_jira_key

    source, effective, _ = dataset
    items = [item for family in effective["families"].values() for item in family["items"] if item.get("synthetic")]
    assert len(items) == 39
    assert all(extract_jira_key(item["content"]) is None for item in items)
    # 원본 O2 내용 속 Jira는 지우지 않는다.
    assert "ETOJ-3257" in source["families"]["o2"]["items"][0]["content"]


def test_item_schema_has_one_optional_nonunique_jira_column():
    schema = (DATABASE / "schema_mysql5619.sql").read_text(encoding="utf-8")
    assert "jira_code VARCHAR(200) NULL" in schema
    assert "jira_key VARCHAR" not in schema
    assert "UNIQUE KEY ix_item_jira_code" not in schema
