"""저장된 관측의 값을 바꾸지 않고 공통 판정 입력으로 변환한다."""

from collections.abc import Collection, Iterable
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.equipment.schemas import StoredSnapshot
from setron.features.storage.inputs import identifier, require, required_text


def read_pm_versions(snapshot: StoredSnapshot) -> tuple[PmVersion, ...]:
    """관측된 SW·PM 관계를 유지하며 판정 입력으로 변환한다.

    param
    - snapshot: 특정 관측일의 서비스 결과. PM 없는 SW는 확인된 대표 모델이 필요하다.

    return
    - 모든 SW·PM의 튜플. 날짜·출처·분석 제외 정보는 원본 snapshot에서 별도로 다룬다.

    raises
    - StorageValidationError: 관측일·모델·SW가 잘못됐거나 PM 위치 중복·부분 연결이 있는 경우.
    """
    require(isinstance(snapshot, StoredSnapshot), "저장된 관측 결과가 필요합니다.")
    require(type(snapshot.base_date) is date, "관측 기준일이 필요합니다.")
    if any(sw.pms for sw in snapshot.software):
        require(all(sw.pms for sw in snapshot.software), "모든 SW의 PM 연결을 확인해야 합니다.")
    result, seen = [], set()
    for sw in snapshot.software:
        identifier(sw.family_id, "family_id")
        required_text(sw.version_raw, "version_raw", 100)
        if sw.release_version_id is not None:
            identifier(sw.release_version_id, "release_version_id")
        for pm in sw.pms:
            required_text(pm.pm_name, "pm_name", 50)
        positions = tuple((pm.pm_name, pm.group_id) for pm in sw.pms) if sw.pms else ((None, snapshot.group_id),)
        for name, model_id in positions:
            identifier(model_id, "group_id")
            if name is not None:
                required_text(name, "pm_name", 50)
            key = ("pm", name) if name is not None else ("family", sw.family_id)
            require(key not in seen, "PM 위치 또는 같은 계열의 대표 SW 연결이 중복되었습니다.")
            seen.add(key)
            result.append(PmVersion(name, model_id, sw.family_id, sw.version_raw, sw.release_version_id))
    return tuple(result)


def read_equipment_record(snapshot: StoredSnapshot, excluded_group_ids: Collection[int]) -> EquipmentRecord:
    """관측 하나를 집계·위험·우선순위·예측이 받는 설비 단위 입력으로 변환한다.

    분석 제외는 모델의 analysis_enabled로만 정한다. 설비가 쓰는 모델(대표 모델과 PM 모델)이
    모두 제외 모델이면 HPA로 표시하고, 제외 모델과 분석 모델이 섞이면 임의로 정하지 않는다.

    param
    - snapshot: 특정 관측일의 서비스 결과.
    - excluded_group_ids: analysis_enabled = false인 모델 ID 목록.

    return
    - 원본 모델명과 PM·SW 연결을 유지한 EquipmentRecord.

    raises
    - StorageValidationError: 설비·관측 필드가 잘못됐거나, PM 연결이 잘못됐거나, 제외 모델이 섞인 경우.
    """
    pms = read_pm_versions(snapshot)
    excluded = _excluded_groups(excluded_group_ids)
    identifier(snapshot.equipment_id, "equipment_id")
    required_text(snapshot.equipment_code, "equipment_code", 50)
    required_text(snapshot.site, "site", 50)
    require(snapshot.line is None or isinstance(snapshot.line, str) and len(snapshot.line) <= 50,
            "line: 50자 이내의 문자열 또는 None이 필요합니다.")
    required_text(snapshot.model_name, "model_name", 255)
    groups = {pm.group_id for pm in pms}
    if snapshot.group_id is not None:
        groups.add(snapshot.group_id)
    hpa = groups & excluded
    require(not hpa or hpa == groups, f"{snapshot.equipment_code}: 분석 제외 모델과 분석 모델이 한 설비에 섞여 있습니다.")
    return EquipmentRecord(snapshot.equipment_id, snapshot.equipment_code, snapshot.base_date, snapshot.site,
                           snapshot.line, snapshot.model_name, pms, is_hpa=bool(hpa))


def read_equipment_records(snapshots: Iterable[StoredSnapshot],
                           excluded_group_ids: Collection[int]) -> tuple[EquipmentRecord, ...]:
    """여러 관측을 변환한다. 여러 기준일을 함께 받을 수 있다 (예측·추이).

    param
    - snapshots: 관측 결과 목록.
    - excluded_group_ids: analysis_enabled = false인 모델 ID 목록.

    return
    - 입력 순서의 EquipmentRecord 튜플.

    raises
    - StorageValidationError: 각 관측이 잘못됐거나 같은 설비·같은 기준일 관측이 중복된 경우.
    """
    result, seen = [], set()
    for snapshot in snapshots:
        record = read_equipment_record(snapshot, excluded_group_ids)
        key = (record.equipment_id, record.base_date)
        require(key not in seen, f"{record.equipment_code}: 같은 기준일 관측이 중복되었습니다.")
        seen.add(key)
        result.append(record)
    return tuple(result)


def _excluded_groups(group_ids: Collection[int]) -> frozenset[int]:
    """분석 제외 대상을 중복 없는 모델 ID 집합으로 검증한다.

    param
    - group_ids: analysis_enabled가 false인 모델 ID 목록.

    return
    - 입력을 변경하지 않은 모델 ID 집합.

    raises
    - StorageValidationError: 문자열 또는 잘못된 모델 ID를 전달한 경우.
    """
    require(not isinstance(group_ids, (str, bytes)), "분석 제외 모델은 ID 목록이어야 합니다.")
    for group_id in group_ids:
        identifier(group_id, "excluded_group_id")
    return frozenset(group_ids)
