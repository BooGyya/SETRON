"""가져오기 검증에 사용하는 저장 자료와 명시적인 계열 대응."""

from setron.features.reference_data import area_of
from dataclasses import dataclass, field
from typing import Mapping

from setron.features.equipment.schemas import SnapshotInput
from setron.features.extraction.normalization import normalize
from setron.features.extraction.schemas import FamilyKey
from setron.features.ai.schemas import ExtractKind
from setron.features.version.service import FamilyReleases

from .schemas import Issue, IssueLevel


@dataclass(frozen=True)
class ModelReference:
    """등록 모델의 독립적인 확인 자료.

    ivar
    - group_id: DB에 등록된 모델 ID. 계열 관리 모델 ID와 실제 적용 모델을 구분.
    - model_name: DB의 등록 모델명. 확정 별칭은 입력값에서만 정규화해 비교.
    - category: POLY·OXIDE·KCTC·HPA 최상위 분류.
    - parent_group_id: 공유 계열을 관리하는 직계 기본 모델 ID. 기본형은 None.
    - analysis_enabled: 해당 등록 모델의 공통 분석 포함 여부.
    """

    group_id: int
    model_name: str
    category: str
    parent_group_id: int | None = None
    analysis_enabled: bool = True


@dataclass(frozen=True)
class FamilyReference:
    """확인된 계열 및 릴리즈 비교 자료.

    ivar
    - key: 관리 자료에서 확인한 계열 구분. model_name은 계열 식별에 사용하지 않음.
    - releases: 같은 계열의 원문·확정 별칭·확인된 순서.
    - targets: 모델명별 목표 릴리즈 ID. 미지정 모델을 다른 모델의 N으로 채우지 않음.
    - owner_group_id: 계열을 관리하는 등록 모델 ID. 모델 자료가 미연결인 순수 계산은 None 허용.
    - production_area: 'FOUNDRY'면 파운드리용 릴리즈노트 계열(' (파운드리)'). None은 메모리·공통 계열.
    """

    key: FamilyKey
    releases: FamilyReleases
    targets: Mapping[str, int | None] = field(default_factory=dict)
    owner_group_id: int | None = None
    production_area: str | None = None


@dataclass(frozen=True)
class StoredRelease:
    """기존 릴리즈·Item의 현재 관리값.

    ivar
    - family_id, version_raw: 기존 자료를 식별하는 계열 ID와 버전 원문.
    - values: 출시일·원본 Item 번호·내용 등 비교할 현재 관리값.
    """

    family_id: int
    version_raw: str
    values: Mapping[str, str | None]


@dataclass(frozen=True)
class ReferenceData:
    """한 번의 검증에서 함께 읽은 저장 자료.

    ivar
    - equipment_codes: 전체 사업장을 기준으로 조회한 기존 호기 코드.
    - release_versions: 기존 호출자를 위한 계열명별 원문 목록. 동명 계열의 ID 대응에는 사용하지 않음.
    - families: 확인된 계열 구분과 릴리즈 자료. 같은 이름의 다른 계열을 합치지 않음.
    - snapshots: 요청한 호기·날짜에 정확히 일치하는 기존 현황.
    - stored_releases: 기존 릴리즈와 연결 Item의 현재 관리값.
    - catalog_loaded: DB 계열 조회 완료 여부. 빈 조회 결과와 미연결을 구분.
    - models: 별도로 조회한 등록 모델 목록. None은 미연결, 빈 튜플은 조회한 모델이 없음.
    - equipment_sites: 입력 호기의 모든 날짜에 저장된 고정 소속 사업장. 정확한 날짜 현황과 별도로 검사.
    - equipment_classifications: 입력 호기의 날짜별 HPA·공통 분석 제외(True) 또는 분석 포함(False) 분류.
    """

    equipment_codes: frozenset[str]
    release_versions: Mapping[str, frozenset[str]] = field(default_factory=dict)
    families: tuple[FamilyReference, ...] = ()
    snapshots: tuple[SnapshotInput, ...] = ()
    stored_releases: tuple[StoredRelease, ...] = ()
    catalog_loaded: bool = False
    models: tuple[ModelReference, ...] | None = None
    equipment_sites: Mapping[str, frozenset[str]] = field(default_factory=dict)
    equipment_classifications: Mapping[str, Mapping[str, frozenset[bool]]] = field(default_factory=dict)


def _matching_models(row, kind: ExtractKind, reference: ReferenceData) -> tuple[ModelReference, ...]:
    """확정 정규화한 입력 모델명과 분류에 일치하는 등록 모델을 조회한다.

    param
    - row, kind: 현재 검토 행과 가져오는 자료 종류.
    - reference: 입력 모델 확인에 사용할 독립적인 등록 모델 목록.

    return
    - 이름·분류가 일치하는 모델 튜플. 자료가 미연결이거나 입력이 잘못됐으면 빈 튜플.
    """
    if reference.models is None:
        return ()
    values = {name: row.value(name) for name in ("group_name", "model_name")}
    if any(value is not None and not isinstance(value, str) for value in values.values()):
        return ()
    normalized, _, _ = normalize(values, kind, row.row_id, {})
    name, category = normalized.get("model_name"), normalized.get("group_name")
    return tuple(registered for registered in reference.models
                 if registered.model_name == name and registered.category == category)


def model_issue(row, kind: ExtractKind, reference: ReferenceData) -> Issue | None:
    """입력 모델이 해당 분류의 유일한 등록 모델인지 검사한다.

    계열 공유 여부와 독립적으로 검사한다. M5→M-O5처럼 확정된 공통 정규화만
    사용하고, 알 수 없는 모델을 계열 관리 모델이나 비슷한 이름으로 대체하지 않는다.

    param
    - row: 현재 수정값을 가진 검토 행.
    - kind: 설비 현황 또는 릴리즈 노트.
    - reference: 독립적인 등록 모델 목록을 포함한 이번 검증의 기준 자료.

    return
    - 모델 공란·미등록·중복이면 model_name의 차단 오류. 유일하게 확인되거나 모델 목록이
      미연결이면 None. 자료형 검사는 기존 항목 검증에 맡김.
    """
    model = row.value("model_name")
    if reference.models is None:
        return None
    if model is None or isinstance(model, str) and not model.strip():
        return Issue(IssueLevel.ERROR, "등록 모델을 연결하려면 모델명이 필요합니다.",
                     row.row_id, "model_name", row.sources.get("model_name"), code="MODEL_REQUIRED")
    if not isinstance(model, str):
        return None
    matches = _matching_models(row, kind, reference)
    if len(matches) == 1:
        return None
    code = "MODEL_AMBIGUOUS" if matches else "MODEL_NOT_REGISTERED"
    reason = ("같은 모델명·설비군에 대응하는 등록 모델이 여러 개입니다. 등록 자료를 확인하세요."
              if matches else "모델명·설비군에 대응하는 등록 모델이 없습니다. 모델명과 설비군을 확인하세요.")
    return Issue(IssueLevel.ERROR, reason, row.row_id, "model_name", row.sources.get("model_name"), code=code)


def family_key(row, kind: ExtractKind) -> FamilyKey | None:
    """수정 후의 값을 기준으로 확정된 공통 계열 구분을 다시 계산한다.

    param
    - row: 현재 수정값을 조회할 수 있는 검토 행.
    - kind: 설비 현황 또는 릴리즈 노트.

    return
    - C3 UI·KCTC 구조를 구분한 계열 키. 대응 정보가 부족하면 None.
    """
    values = {name: row.value(name) for name in (
        "group_name", "model_name", "family_name", "ui_type", "structure_type",
    )}
    if any(value is not None and not isinstance(value, str) for value in values.values()):
        return None
    return normalize(values, kind, row.row_id, {})[1]


def analysis_excluded(row, kind: ExtractKind, reference: ReferenceData | None) -> bool | None:
    """등록 모델의 분석 제외 설정과 HPA 분류를 저장 단계와 같은 기준으로 판정한다."""
    key = family_key(row, kind)
    if key is None:
        return None
    matches = _matching_models(row, kind, reference) if reference is not None else ()
    return key.category == "HPA" or len(matches) == 1 and not matches[0].analysis_enabled


def identity(key: FamilyKey) -> tuple:
    """형상·생산 영역·개조 모델로 공유 계열을 나누지 않는 식별값을 반환한다.

    param
    - key: 추출 또는 저장 자료의 계열 구분.

    return
    - 분류·계열명·C3 UI·KCTC 구조로 구성한 키.
    """
    return key.category, key.family_name, key.ui_type, key.structure_type


def resolve_family(row, kind: ExtractKind, reference: ReferenceData, *,
                   sheet_name: str | None = None, workbook_name: str | None = None) -> FamilyReference | None:
    """현재 모델 또는 직계 기본 모델이 관리하는 유일한 저장 계열을 찾는다.

    같은 구분의 메모리·파운드리 계열이 함께 있으면(O2·C3·C4) 행의 영역으로 고른다.
    영역은 저장(import_save)과 같은 :func:`reference_data.area_of` 로 정한다.

    param
    - row: 사용자의 수정값을 포함한 검토 행.
    - kind: 가져오는 자료 종류.
    - reference: 이번 검증에서 읽은 저장 자료.
    - sheet_name, workbook_name: 원본 시트명·통합문서 이름. 영역 기본값 판정에 쓴다.

    return
    - 구분값과 소유 범위가 일치하는 유일한 계열. 없거나 여러 개면 None.
      등록 모델 목록이 미연결이면 기존 순수 계산의 구분값 매칭만 수행.
    """
    key = family_key(row, kind)
    if key is None:
        return None
    matches = [family for family in reference.families if identity(family.key) == identity(key)]
    if reference.models is not None:
        models = _matching_models(row, kind, reference)
        if len(models) != 1:
            return None
        model = models[0]
        owners = {model.group_id}
        if model.parent_group_id is not None:
            owners.add(model.parent_group_id)
        matches = [family for family in matches if family.owner_group_id in owners]
    if any(family.production_area == "FOUNDRY" for family in matches):
        values = {name: row.value(name) for name in ("production_area", "site", "line")}
        want = "FOUNDRY" if area_of(values, sheet_name, workbook_name) == "FOUNDRY" else None
        matches = [family for family in matches if family.production_area == want]
    return matches[0] if len(matches) == 1 else None
