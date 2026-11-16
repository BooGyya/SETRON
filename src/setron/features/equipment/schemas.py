"""검토가 끝난 한 기준일의 저장 입력과 원문을 보존하는 조회 결과."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, kw_only=True)
class PmInput:
    """PM 저장 요청 데이터.

    ivar
    - pm_name: 설비 내 고정 위치 이름. 예: PM1, PM2.
    - group_id: 해당 PM에 적용된 모델 ID.
    - model_name_raw: 원본에 기록된 PM 모델명. 값이 없으면 None.
    - source_reference: 필드별 원본 시트·셀 위치. 출처가 없으면 None.
    """

    pm_name: str
    group_id: int
    model_name_raw: str | None = None
    source_reference: dict | None = None


@dataclass(frozen=True, kw_only=True)
class SoftwareInput:
    """설비 적용 SW 저장 요청 데이터.

    ivar
    - family_id: 적용 SW의 버전 계열 ID.
    - version_raw: 원본에 기록된 SW 버전 문자열.
    - release_version_id: 같은 버전으로 확인된 릴리즈 ID. 미등록·미확인은 None.
    - source_reference: 필드별 원본 시트·셀 위치. 출처가 없으면 None.
    - pms: 이 SW를 함께 사용하는 PM 목록. PM 구분이 없는 일반 설비는 빈 튜플.
    """

    family_id: int
    version_raw: str
    # 같은 버전으로 확인한 대응만 지정한다. 미등록/미확인은 None을 유지한다.
    release_version_id: int | None = None
    source_reference: dict | None = None
    pms: tuple[PmInput, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SnapshotInput:
    """기준일별 설비 현황 저장 요청 데이터.

    ivar
    - equipment_code: 전체 사업장에서 실제 설비를 식별하는 호기 코드.
    - base_date: 현황의 기준 날짜.
    - site: 설비의 고정 소속 사업장. 다른 날짜의 입력으로도 변경할 수 없음.
    - model_name: 원본에 기록된 설비 모델명. 혼합형 표기도 그대로 보관.
    - batch_id: 원본 자료를 등록한 입력 작업 ID.
    - software: 계열별 적용 SW와 연결된 PM 목록.
    - line: 기준 날짜의 라인. 값이 없으면 None.
    - group_id: 확인된 대표 모델 ID. 혼합 설비 등 대표 모델을 정할 수 없으면 None.
    - production_area: 데이터 관리의 생산 분류 코드. 확인되지 않았으면 None.
    - structure_type: INLINE, CLUSTER 등 설비 구조. 값이 없으면 None.
    - geometry_type: 5각, 6각 등 원본 형상 표기. 값이 없으면 None.
    - modification_type: CIP 등 개조 유형. 값이 없으면 None.
    - source_reference: 필드별 원본 시트·셀 위치. 출처가 없으면 None.
    """

    equipment_code: str
    base_date: date
    site: str
    model_name: str
    batch_id: int
    software: tuple[SoftwareInput, ...]
    line: str | None = None
    group_id: int | None = None
    production_area: str | None = None
    structure_type: str | None = None
    geometry_type: str | None = None
    modification_type: str | None = None
    source_reference: dict | None = None
    process_name: str | None = None          # 공정 원문(11/13 멘토: 복합공정은 AAAA/BBBB · AAAA & BBBB)

    @property
    def production_area_label(self) -> str:
        """생산 영역을 화면에 표시할 이름으로 변환한다.

        return
        - 관리한 생산 분류 이름. 생산 영역이 None이면 '미확인'.
        """
        from setron.features.production_area import production_area_label
        return production_area_label(self.production_area, unknown="미확인")


@dataclass(frozen=True, kw_only=True)
class StoredPm(PmInput):
    """PM 조회 결과 데이터.

    ivar
    - pm_id: 해당 날짜의 PM 기록 ID. 날짜 간 위치 연결에는 equipment_id와 pm_name을 사용.
    - batch_id: 해당 PM 정보를 등록한 입력 작업 ID.
    """

    pm_id: int
    batch_id: int


@dataclass(frozen=True, kw_only=True)
class StoredSoftware(SoftwareInput):
    """설비 적용 SW 조회 결과 데이터.

    ivar
    - equipment_version_id: DB에 저장된 적용 SW 기록 ID.
    - batch_id: 해당 SW 정보를 등록한 입력 작업 ID.
    - pms: 이 SW 기록을 공유하는 PM 조회 결과 목록.
    """

    equipment_version_id: int
    batch_id: int
    pms: tuple[StoredPm, ...] = ()


@dataclass(frozen=True, kw_only=True)
class EquipmentSourceCell:
    """현황 또는 PM 필드의 실제 입력 작업과 원본 셀 조회 결과."""

    field: str
    batch_id: int
    sheet: str
    address: str
    value: object
    available: bool


@dataclass(frozen=True, kw_only=True)
class StoredSnapshot(SnapshotInput):
    """기준일별 설비 현황 조회 결과 데이터.

    ivar
    - equipment_id: 실제 설비 ID. 날짜나 개조 상태가 바뀌어도 유지.
    - snapshot_id: 특정 날짜의 설비 현황 ID. 날짜가 다르면 별도 기록으로 구분.
    - software: 해당 날짜의 적용 SW 및 PM 조회 결과 목록.
    """

    equipment_id: int
    snapshot_id: int
    software: tuple[StoredSoftware, ...]

    @property
    def pm_mismatch_families(self) -> tuple[int, ...]:
        """같은 계열의 PM 사이에 SW 버전 원문이 다른 계열을 조회한다.

        O2와 O3처럼 서로 다른 계열의 버전 차이는 불일치로 처리하지 않는다.
        버전 원문은 대소문자와 공백까지 구분하며 릴리즈의 선후관계는 판단하지 않는다.

        return
        - 서로 다른 SW 원문을 가진 PM이 있는 계열 ID 튜플. 불일치가 없으면 빈 튜플.
        """
        versions = {}
        for software in self.software:
            if software.pms:
                versions.setdefault(software.family_id, set()).add(software.version_raw)
        return tuple(family for family, values in versions.items() if len(values) > 1)
