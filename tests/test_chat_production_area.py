"""저장된 생산 구분이 같은 이름의 라인·PM 필터·과거 조회에서도 보존되는지 검증한다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from setron.features import reference_data
from setron.features.aggregation.schemas import EquipmentRecord
from setron.features.ai.schemas import QueryConditions
from setron.features.chat.answers import ChatData, _scope
from setron.features.chat.schemas import ChatRequest, Intent
from setron.features.equipment.read import read_equipment_record as service_record
from setron.features.equipment.schemas import StoredPm, StoredSnapshot, StoredSoftware
from setron.features.query.schemas import FamilyChoice, GroupChoice, QueryCatalog
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.read_converters import read_equipment_record as ui_record

EARLIER, LATER = date(2026, 9, 21), date(2026, 10, 21)
LINE = "NRD (MR2동)"


def snapshot(equipment_id, code, area, *, day=LATER, mixed=False, hpa=False):
    if mixed:
        software = tuple(StoredSoftware(equipment_version_id=family * 10, batch_id=7,
            family_id=family, version_raw=raw, release_version_id=family * 100 + 1,
            pms=tuple(StoredPm(pm_id=number, batch_id=7, pm_name=f"PM{number}", group_id=family,
                              model_name_raw=model) for number in numbers))
            for family, raw, numbers, model in ((3, "v4.50.1.1_a", (1, 2), "M-O3"), (2, "v3.53_F2", (3, 4), "M-O2")))
    else:
        family = 9 if hpa else 1
        software = (StoredSoftware(equipment_version_id=family * 10, batch_id=7, family_id=family,
                    version_raw="v2.0" if hpa else "v3.52_n4", release_version_id=None if hpa else 101),)
    return StoredSnapshot(equipment_id=equipment_id, snapshot_id=equipment_id * 100,
        equipment_code=code, base_date=day, site="평택" if hpa else "화성", line="S5" if hpa else LINE,
        model_name="HPA" if hpa else "M-O2/O3" if mixed else "M-C3", batch_id=7,
        group_id=None if mixed else 9 if hpa else 1, production_area=area, software=software)


def data(records):
    families = {family_id: FamilyReleases(family_id, (ReleaseVersion(family_id * 100 + 1, family_id, raw, 10, EARLIER),))
                for family_id, raw in ((1, "v3.52_n4"), (2, "v3.53_F2"), (3, "v4.50.1.1_a"))}
    catalog = QueryCatalog(sites=("평택", "화성"), lines=(LINE, "S5", "15L"),
        groups=(GroupChoice(1, "C3", "M-C3", "POLY"), GroupChoice(2, "O2", "M-O2", "OXIDE"),
                GroupChoice(3, "O3", "M-O3", "OXIDE")),
        families=tuple(FamilyChoice(family_id, name, family_id) for family_id, name in ((1, "C3"), (2, "O2"), (3, "O3"))),
        dates=tuple(sorted({record.base_date for record in records})), releases=families)
    return ChatData(tuple(records), catalog, {(family_id, family_id): family_id * 100 + 1 for family_id in families})


def request(area=None, **conditions):
    return ChatRequest("생산 구분별 설비", (Intent.SEARCH,), conditions=QueryConditions(**conditions),
                       areas=(area,) if area else ())


@pytest.fixture
def registered_order(monkeypatch, request):
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", {})
    reference_data.use_registered_lines([{"site": "화성", "line": LINE, "fab": area} for area in request.param])


@pytest.mark.parametrize("reader", [service_record, ui_record])
@pytest.mark.parametrize("area", ["MEMORY", "FOUNDRY", None])
def test_both_read_converters_preserve_explicit_area_and_do_not_modify_snapshot(reader, area):
    original = snapshot(1, "ESP444", area)
    before = deepcopy(original)
    record = reader(original, {9})
    assert record.production_area == area
    assert original == before


def test_eighth_positional_argument_keeps_hpa_meaning():
    record = EquipmentRecord(1, "ESHC13", LATER, "평택", "S5", "HPA", (), True)
    assert record.is_hpa is True and record.production_area is None


@pytest.mark.parametrize("registered_order", [("MEMORY", "FOUNDRY"), ("FOUNDRY", "MEMORY")], indirect=True)
def test_same_site_and_line_are_filtered_by_each_snapshot_area_regardless_of_registry_order(registered_order):
    memory = service_record(snapshot(1, "ESP444", "MEMORY"), {9})
    foundry = service_record(snapshot(2, "ESON81", "FOUNDRY", mixed=True), {9})
    dataset = data((memory, foundry))
    assert [record.equipment_code for record in _scope(request("MEMORY"), dataset).records] == ["ESP444"]
    assert [record.equipment_code for record in _scope(request("FOUNDRY"), dataset).records] == ["ESON81"]
    assert {record.equipment_code for record in _scope(request(), dataset).records} == {"ESP444", "ESON81"}


@pytest.mark.parametrize("registered_order", [("MEMORY", "FOUNDRY"), ("FOUNDRY", "MEMORY")], indirect=True)
def test_model_pm_filter_keeps_foundry_area_and_only_the_requested_pms(registered_order):
    foundry = service_record(snapshot(2, "ESON81", "FOUNDRY", mixed=True), {9})
    result = _scope(request("FOUNDRY", models=("M-O2",)), data((foundry,)))
    assert len(result.records) == 1
    record = result.records[0]
    assert record.production_area == "FOUNDRY"
    assert [(pm.pm_name, pm.group_id) for pm in record.pms] == [("PM3", 2), ("PM4", 2)]
    assert len(foundry.pms) == 4


def test_latest_observation_area_is_used_before_filtering_and_history_keeps_its_own_area(monkeypatch):
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", {})
    earlier = service_record(snapshot(1, "ESP444", "MEMORY", day=EARLIER), {9})
    later = service_record(snapshot(1, "ESP444", "FOUNDRY", day=LATER), {9})
    dataset = data((earlier, later))
    assert _scope(request("MEMORY"), dataset).records == ()
    assert _scope(request("FOUNDRY"), dataset).records == (later,)
    assert _scope(request("MEMORY", base_date=EARLIER.isoformat()), dataset).records == (earlier,)
    assert _scope(request("FOUNDRY", base_date=EARLIER.isoformat()), dataset).records == ()


@pytest.mark.parametrize("area", ["MEMORY", "FOUNDRY"])
def test_hpa_without_area_is_excluded_from_production_scope_but_remains_in_unfiltered_scope(area):
    hpa = service_record(snapshot(3, "ESHC13", None, hpa=True), {9})
    dataset = data((hpa,))
    assert hpa.is_hpa and hpa.production_area is None
    scoped = _scope(request(area), dataset)
    assert scoped.records == () and scoped.hpa == 0
    unfiltered = _scope(request(), dataset)
    assert unfiltered.records == (hpa,) and unfiltered.hpa == 1


def test_legacy_record_without_explicit_area_still_uses_existing_known_line_fallback(monkeypatch):
    monkeypatch.setattr(reference_data, "REGISTERED_AREAS", {})
    record = service_record(snapshot(1, "ESP444", None), {9})
    record = replace(record, line="15L")
    dataset = data((record,))
    assert _scope(request("MEMORY"), dataset).records == (record,)
    assert _scope(request("FOUNDRY"), dataset).records == ()
