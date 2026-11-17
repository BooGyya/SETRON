"""설비·고정 PM 위치를 연결하되 미관측·범위 이탈·계열 변경을 버전 변경과 구분한다."""

from collections.abc import Mapping
from datetime import date

from setron.features.equipment.read import read_pm_versions
from setron.features.storage.inputs import require
from setron.features.version.schemas import CompareReason, CompareStatus
from setron.features.version.service import FamilyReleases, compare_version

from .comparison_schemas import (
    ChangeReason, ChangeStatus, HistoryComparison, VersionChange, VersionDirection, VersionObservation,
)
from .schemas import HistoryFrame


def compare_frames(
    before: HistoryFrame,
    after: HistoryFrame,
    families: Mapping[int, FamilyReleases] | None = None,
) -> HistoryComparison:
    """일반 SW는 설비·계열, PM은 설비·고정 위치로 비교하는 순수 함수.

    확인된 릴리즈 대응은 같은 버전으로 처리한다. 그 외의 원문 차이는 변경 사실로 전달하되
    방향은 BE-003의 확인된 순서만 사용한다. 원문·현황·조회 범위를 수정하지 않는다.
    """
    require(isinstance(before, HistoryFrame) and isinstance(after, HistoryFrame), "비교할 두 날짜의 이력 자료가 필요합니다.")
    require(type(before.base_date) is date and type(after.base_date) is date, "비교 기준일은 날짜여야 합니다.")
    require(before.base_date < after.base_date, "이전 날짜는 이후 날짜보다 앞서야 합니다.")
    families = {} if families is None else families
    require(isinstance(families, Mapping), "계열별 릴리즈 목록이 필요합니다.")
    require(all(isinstance(family, FamilyReleases) and family.family_id == key for key, family in families.items()),
            "릴리즈 목록과 버전 계열이 일치해야 합니다.")
    left, right = _index(before), _index(after)
    changes = []
    for equipment_id in sorted(left.keys() | right.keys()):
        old, new = left.get(equipment_id), right.get(equipment_id)
        if old and new:
            require(old[0].snapshot.equipment_code == new[0].snapshot.equipment_code,
                    "같은 설비 ID의 호기 코드가 일치하지 않습니다.")
        selected_before = old[2] if old else {}
        selected_after = new[2] if new else {}
        for key in sorted(selected_before.keys() | selected_after.keys()):
            first, last = selected_before.get(key), selected_after.get(key)
            reason = _missing_reason(key, old, new, first, last)
            if reason is not None:
                status, direction = ChangeStatus.UNDETERMINED, VersionDirection.UNKNOWN
            else:
                status, direction, reason = _compare_versions(first, last, families)
            snapshot = (new or old)[0].snapshot
            changes.append(VersionChange(
                equipment_id, snapshot.equipment_code, key[1] if key[0] == "pm" else None,
                before.base_date, after.base_date, first, last, status, direction, reason,
            ))
    return HistoryComparison(before, after, tuple(changes))


def _index(frame):
    """범위에 포함된 SW 연결과 원본 전체 위치를 별도로 보존해 범위 이탈을 구분한다."""
    result = {}
    for match in frame.matches:
        snapshot = match.snapshot
        require(snapshot.base_date == frame.base_date, "비교 자료의 관측 날짜가 프레임 날짜와 다릅니다.")
        require(snapshot.equipment_id not in result, "같은 설비·날짜의 이력이 중복되었습니다.")
        pms = read_pm_versions(snapshot)
        pairs = {(pm.group_id, pm.family_id) for pm in pms}
        require(set(match.model_family_pairs) <= pairs, "조회 범위에 실제 SW·PM 연결이 아닌 조합이 있습니다.")
        all_positions, selected = {}, {}
        for software in snapshot.software:
            for pm in software.pms or (None,):
                key = ("pm", pm.pm_name) if pm else ("family", software.family_id)
                value = VersionObservation(snapshot, software, pm)
                all_positions[key] = value
                model_id = pm.group_id if pm else snapshot.group_id
                if (model_id, software.family_id) in match.model_family_pairs:
                    selected[key] = value
        result[snapshot.equipment_id] = match, all_positions, selected, any(sw.pms for sw in snapshot.software)
    return result


def _missing_reason(key, old, new, first, last):
    if old is None:
        return ChangeReason.MISSING_BEFORE_SNAPSHOT
    if new is None:
        return ChangeReason.MISSING_AFTER_SNAPSHOT
    if old[3] != new[3]:
        return ChangeReason.TRACKING_CHANGED
    if first is None:
        return ChangeReason.OUTSIDE_BEFORE_SCOPE if key in old[1] else ChangeReason.MISSING_BEFORE_POSITION
    if last is None:
        return ChangeReason.OUTSIDE_AFTER_SCOPE if key in new[1] else ChangeReason.MISSING_AFTER_POSITION
    if first.software.family_id != last.software.family_id:
        return ChangeReason.FAMILY_CHANGED
    return None


def _compare_versions(first, last, families):
    old, new = first.software, last.software
    family = families.get(old.family_id)
    before_release = _release(family, old)
    after_release = _release(family, new)
    same_link = old.release_version_id is not None and old.release_version_id == new.release_version_id
    if old.version_raw == new.version_raw or same_link or (
        before_release is not None and after_release is not None
        and before_release.release_version_id == after_release.release_version_id
    ):
        return ChangeStatus.UNCHANGED, VersionDirection.UNCHANGED, None
    if before_release is None or after_release is None:
        return ChangeStatus.CHANGED, VersionDirection.UNKNOWN, ChangeReason.RELEASE_UNCONFIRMED
    # BE-003를 재사용해 중복 순서·미확인 순서에서 방향을 추정하지 않는다.
    comparison = compare_version(family, new.version_raw, before_release.release_version_id,
                                 release_version_id=after_release.release_version_id)
    if comparison.status is CompareStatus.AT_OR_ABOVE:
        return ChangeStatus.CHANGED, VersionDirection.UPGRADE, None
    if comparison.status is CompareStatus.BELOW:
        return ChangeStatus.CHANGED, VersionDirection.DOWNGRADE, None
    involved_orders = {before_release.version_order, after_release.version_order}
    conflict = bool(involved_orders.intersection(family.sorted.duplicated_orders))
    reason = ChangeReason.ORDER_CONFLICT if conflict or comparison.reason is CompareReason.ORDER_CONFLICT else ChangeReason.ORDER_UNKNOWN
    return ChangeStatus.CHANGED, VersionDirection.UNKNOWN, reason


def _release(family, software):
    if family is None:
        return None
    if software.release_version_id is not None:
        return family.get(software.release_version_id)
    return family.match(software.version_raw)
