"""DB 없이 BE-002의 원문 보존·중복 병합·PM 충돌·필수값을 검증한다."""

from dataclasses import replace
from datetime import date, datetime

import pytest

from setron.features.equipment.schemas import PmInput, SnapshotInput, SoftwareInput
from setron.features.equipment.validation import merge_sources, prepare_snapshot
from setron.features.storage.validation import StorageValidationError


def snapshot(**changes):
    """기본 설비 현황을 생성하고 지정한 항목을 테스트 값으로 교체한다.

    :param changes: 기본값 대신 사용할 항목 이름과 값.
    :return: 테스트용 SnapshotInput.
    """
    values = dict(
        equipment_code="EQ-001", base_date=date(2026, 10, 27), site="화성", model_name="M-O2",
        batch_id=1, group_id=1, software=(SoftwareInput(family_id=1, version_raw="v1"),),
    )
    values.update(changes)
    return SnapshotInput(**values)


def test_optional_production_area_and_exact_originals_are_preserved():
    """Memory/Foundry를 몰라도 허용하고, 호기·모델·SW의 원문과 공백을 그대로 유지하는지 확인한다."""
    original = snapshot(equipment_code="eq-001 ", model_name="M5", geometry_type="5각 원문",
                        software=(SoftwareInput(family_id=1, version_raw=" V1_d5 "),))
    saved = prepare_snapshot(original)
    assert saved == original
    assert saved.production_area is None
    assert saved.production_area_label == "미확인"
    assert prepare_snapshot(replace(original, production_area="FOUNDRY")).production_area_label == "Foundry"


@pytest.mark.parametrize("changes", [
    {"equipment_code": " "}, {"equipment_code": "x" * 51}, {"site": ""}, {"model_name": None},
    {"base_date": "2026-10-27"}, {"base_date": datetime(2026, 10, 27)},
    {"batch_id": True}, {"batch_id": 0}, {"group_id": "1"}, {"production_area": ""},
    {"production_area": []}, {"software": ()}, {"software": []}, {"line": 1},
    {"software": (SoftwareInput(family_id=1, version_raw=" "),)},
    {"software": (SoftwareInput(family_id=False, version_raw="v1"),)},
    {"software": (SoftwareInput(family_id=1, version_raw="v1", release_version_id=0),)},
    {"software": (SoftwareInput(family_id=1, version_raw="v1", pms=(PmInput(pm_name="", group_id=1),)),)},
    {"software": (SoftwareInput(family_id=1, version_raw="v1", pms=(PmInput(pm_name="PM1", group_id=True),)),)},
    {"group_id": None},
])
def test_invalid_required_values_are_not_guessed(changes):
    """필수값 누락이나 잘못된 자료형을 검증 오류로 처리하는지 확인한다.

    :param changes: 잘못된 입력을 재현하기 위한 현황 항목과 값.
    """
    with pytest.raises(StorageValidationError):
        prepare_snapshot(snapshot(**changes))


def test_duplicate_sw_and_pm_merge_all_cells_without_changing_input():
    """중복 SW와 PM을 합쳐도 모든 시트의 출처가 남고, 원래 입력 객체는 바뀌지 않는지 확인한다."""
    source = {"version_raw": {"sheet": "원본", "cells": ["B2"]}}
    software = SoftwareInput(family_id=1, version_raw="v1", source_reference=source,
                             pms=(PmInput(pm_name="PM1", group_id=1, source_reference=source),))
    other_source = {"version_raw": {"sheet": "다른 원본", "cells": ["C5"]}}
    value = snapshot(software=(software, replace(software, source_reference=other_source,
                     pms=(replace(software.pms[0], source_reference=other_source), PmInput(pm_name="PM2", group_id=1)))))
    saved = prepare_snapshot(value)
    assert len(saved.software) == 1
    assert [pm.pm_name for pm in saved.software[0].pms] == ["PM1", "PM2"]
    expected = {"version_raw": [source["version_raw"], other_source["version_raw"]]}
    assert saved.software[0].source_reference == expected
    assert saved.software[0].pms[0].source_reference == expected
    saved.software[0].source_reference["version_raw"][0]["cells"].append("Z9")
    assert source["version_raw"]["cells"] == ["B2"]
    assert len(value.software) == 2


def test_same_sheet_cells_are_unioned_exactly():
    """같은 시트의 출처를 합칠 때 겹친 셀은 한 번만 남기고 나머지 셀도 보존하는지 확인한다."""
    assert merge_sources({"version_raw": {"sheet": "현황", "cells": ["B2", "C2"]}},
                         {"version_raw": {"sheet": "현황", "cells": ["C2", "D2"]}}) == {
        "version_raw": {"sheet": "현황", "cells": ["B2", "C2", "D2"]},
    }


@pytest.mark.parametrize("second", [
    SoftwareInput(family_id=1, version_raw="v3", pms=(PmInput(pm_name="PM1", group_id=1),)),
    SoftwareInput(family_id=2, version_raw="v1", pms=(PmInput(pm_name="PM1", group_id=1),)),
    SoftwareInput(family_id=1, version_raw="v1", pms=(PmInput(pm_name="PM1", group_id=2),)),
])
def test_same_fixed_pm_position_with_conflicting_sw_family_or_model_is_blocked(second):
    """같은 PM 위치에 상충하는 SW·계열·모델을 배정하면 저장을 차단하는지 확인한다.

    :param second: 기존 PM1 기록과 충돌하도록 구성한 두 번째 SW 입력.
    """
    first = SoftwareInput(family_id=1, version_raw="v1", pms=(PmInput(pm_name="PM1", group_id=1),))
    with pytest.raises(StorageValidationError, match="PM1"):
        prepare_snapshot(snapshot(software=(first, second)))


def test_different_pms_may_have_different_versions_in_the_same_family():
    """같은 계열이라도 PM1과 PM2의 버전이 다른 상황은 정상 입력으로 허용하는지 확인한다."""
    first = SoftwareInput(family_id=1, version_raw="v1", pms=(PmInput(pm_name="PM1", group_id=1),))
    second = SoftwareInput(family_id=1, version_raw="v3", pms=(PmInput(pm_name="PM2", group_id=1),))
    assert len(prepare_snapshot(snapshot(software=(first, second))).software) == 2


def test_missing_pm_connections_or_general_version_conflicts_need_review():
    """SW의 PM 연결이 일부 빠지거나 일반 설비의 한 계열에 버전이 여러 개면 검토를 요청하는지 확인한다."""
    pm = SoftwareInput(family_id=1, version_raw="v1", pms=(PmInput(pm_name="PM1", group_id=1),))
    for software in ((pm, SoftwareInput(family_id=2, version_raw="v1")),
                     (SoftwareInput(family_id=1, version_raw="v1"), SoftwareInput(family_id=1, version_raw="v3"))):
        with pytest.raises(StorageValidationError):
            prepare_snapshot(snapshot(software=software))


@pytest.mark.parametrize("source", [[], {"version_raw": []}, {"version_raw": {"sheet": "현황"}},
    {"version_raw": {"sheet": "현황", "cells": []}}, {"version_raw": {"sheet": "", "cells": ["A1"]}},
    {"version_raw": {"sheet": "현황", "cells": [None]}}])
def test_malformed_source_locations_are_blocked(source):
    """출처의 시트명이나 셀 목록이 잘못된 경우 검증 오류가 발생하는지 확인한다.

    :param source: 시트명·셀 목록이 누락됐거나 형식이 잘못된 출처.
    """
    with pytest.raises(StorageValidationError):
        prepare_snapshot(snapshot(source_reference=source))
