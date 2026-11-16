"""저장할 현황의 필수값·PM 충돌을 확인하고 정확한 중복만 합친다."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import re

from setron.features.storage.inputs import identifier, merge_sources, require, required_text

from .schemas import PmInput, SnapshotInput, SoftwareInput


def prepare_snapshot(value: SnapshotInput) -> SnapshotInput:
    """설비 현황 입력을 검증하고 중복된 SW·PM·출처를 정리한다.

    계열과 버전 원문이 정확히 같은 SW만 합친다. 같은 PM 위치의 상충 값은 차단하고,
    서로 다른 PM의 버전 차이는 허용한다. DB의 연결 정보는 저장 서비스에서 확인한다.

    param
    - value: 검증할 설비 현황 저장 요청.

    return
    - 원본 입력을 변경하지 않고 검증과 중복 정리를 마친 SnapshotInput 복사본.

    raises
    - StorageValidationError: 필수값·자료형·출처·PM 연결에 오류가 있는 경우.
    """
    require(isinstance(value, SnapshotInput), "SnapshotInput 형식이 필요합니다.")
    # 중복을 합치는 동안에도 호출자가 가진 원본 입력은 그대로 남겨둔다.
    value = deepcopy(value)
    required_text(value.equipment_code, "equipment_code", 50)
    required_text(value.site, "site", 50)
    required_text(value.model_name, "model_name", 255)
    require(type(value.base_date) is date, "base_date: 기준 날짜가 필요합니다.")
    identifier(value.batch_id, "batch_id")
    if value.group_id is not None:
        identifier(value.group_id, "group_id")
    require(value.production_area is None or (
        isinstance(value.production_area, str) and (value.production_area in {"MEMORY", "FOUNDRY"}
                                                   or re.fullmatch(r"A[0-9]{9}", value.production_area))
    ), "production_area: 등록된 생산 분류 코드 또는 미확인(None)이어야 합니다.")
    for field, limit in (("line", 50), ("structure_type", 50), ("geometry_type", 50), ("modification_type", 100)):
        text = getattr(value, field)
        require(text is None or isinstance(text, str) and len(text) <= limit, f"{field}: 문자열 또는 None이 필요합니다.")
    require(isinstance(value.software, tuple) and bool(value.software), "적용 SW 버전이 하나 이상 필요합니다.")
    software_by_key, pm_positions = {}, {}
    for software in value.software:
        require(isinstance(software, SoftwareInput), "SoftwareInput 형식이 필요합니다.")
        identifier(software.family_id, "family_id")
        required_text(software.version_raw, "version_raw", 100)
        if software.release_version_id is not None:
            identifier(software.release_version_id, "release_version_id")
        require(isinstance(software.pms, tuple), "PM은 tuple로 전달해야 합니다.")
        # 계열이 다르거나 원문 표기가 다르면 별개의 SW 기록으로 유지한다.
        key = (software.family_id, software.version_raw)
        previous = software_by_key.get(key)
        if previous is not None:
            require(previous.release_version_id == software.release_version_id, "같은 적용 SW의 릴리즈 연결이 상충합니다.")
            require(bool(previous.pms) == bool(software.pms), "PM 구분 유무가 상충합니다.")
        pms = {pm.pm_name: pm for pm in previous.pms} if previous else {}
        for pm in software.pms:
            require(isinstance(pm, PmInput), "PmInput 형식이 필요합니다.")
            required_text(pm.pm_name, "pm_name", 50)
            identifier(pm.group_id, "PM group_id")
            require(pm.model_name_raw is None or isinstance(pm.model_name_raw, str) and len(pm.model_name_raw) <= 255,
                    "model_name_raw: 255자 이내의 문자열이 필요합니다.")
            # PM 이름은 현황 전체에서 한 위치를 뜻하므로 다른 SW에 중복 배정되면 안 된다.
            identity = (key, pm.group_id, pm.model_name_raw)
            require(pm.pm_name not in pm_positions or pm_positions[pm.pm_name] == identity,
                    f"{pm.pm_name}: 같은 PM 위치에 상충하는 모델·계열·SW 값이 있습니다.")
            pm_positions[pm.pm_name] = identity
            old = pms.get(pm.pm_name)
            pms[pm.pm_name] = replace(pm, source_reference=merge_sources(
                old.source_reference if old else None, pm.source_reference,
                allow_batch=True,
            ))
        software_by_key[key] = replace(software, pms=tuple(pms.values()), source_reference=merge_sources(
            previous.source_reference if previous else None, software.source_reference,
            allow_batch=True,
        ))
    normalized = tuple(software_by_key.values())
    # PM 정보가 일부만 있으면 나머지 SW의 적용 위치를 추정하지 않고 확인을 요청한다.
    if any(software.pms for software in normalized):
        require(all(software.pms for software in normalized), "PM이 있는 현황에서는 모든 SW의 PM 연결을 확인해야 합니다.")
    else:
        require(value.group_id is not None, "PM 구분이 없는 설비에는 확인된 대표 모델이 필요합니다.")
        require(len({software.family_id for software in normalized}) == len(normalized),
                "PM 구분 없이 같은 계열의 여러 SW 버전을 저장할 수 없습니다.")
    return replace(value, software=normalized, source_reference=merge_sources(value.source_reference, allow_batch=True))
