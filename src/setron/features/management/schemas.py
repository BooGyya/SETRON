"""저장된 관리값의 검토·충돌 검사와 묶음 저장 계약 (BE-025)."""

from dataclasses import dataclass
from datetime import date

from setron.features.aging.schemas import AgeThresholds, EquipmentAge
from setron.features.equipment.schemas import EquipmentSourceCell
from setron.features.release.refresh_schemas import ReleaseRefresh
from setron.features.targets.schemas import CandidateConfirmation, TargetPair


@dataclass(frozen=True)
class ManagedRecord:
    """현재 관리값·보관 원본·편집 가능 항목과 재검토에 사용할 지문."""

    table: str
    key: dict
    current: dict
    editable_fields: tuple[str, ...]
    original_cells: tuple[EquipmentSourceCell, ...]
    revision: str


@dataclass(frozen=True)
class RecordEdit:
    """get_record/list_records에서 읽은 revision과 사용자가 바꾼 항목만 전달한다.

    PM의 family_id·version_raw·release_version_id는 선택한 PM의 SW만 수정한다.
    PM 위치·기준일·원본·기본키는 편집할 수 없다.
    """

    table: str
    key: dict
    revision: str
    changes: dict


@dataclass(frozen=True)
class ApplicabilityView:
    """모델·계열별 적용 확인 근거의 조회 결과와 충돌 검사 지문."""

    value: TargetPair
    revision: str


@dataclass(frozen=True)
class ApplicabilityEdit:
    """모델별 적용 확인 근거를 같은 수정 묶음에 포함한다. 목표 N은 저장하지 않는다."""

    model_id: int
    family_id: int
    revision: str
    candidates: tuple[CandidateConfirmation, ...]


@dataclass(frozen=True)
class ManagementResult:
    """commit 성공 후 전달하는 최신 관리값·판정·노후도. UI는 이 결과로 재조회한다."""

    records: tuple[ManagedRecord, ...]
    refresh: ReleaseRefresh
    ages: tuple[tuple[date, tuple[EquipmentAge, ...]], ...]
    thresholds: AgeThresholds | None
    audit_batch_id: int
