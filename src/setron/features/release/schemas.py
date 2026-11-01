"""릴리즈 관리 요청 및 원본 비교 데이터."""

from dataclasses import dataclass
from datetime import date, datetime

from setron.features.item.schemas import ItemState, TargetScope


@dataclass(frozen=True, kw_only=True)
class ReleaseInput:
    """릴리즈 등록 요청 데이터.

    :ivar family_id: 확인된 버전 계열 ID.
    :ivar version_raw: 원본 SW 버전 문자열.
    :ivar batch_id: 원본 입력 작업 ID. None이면 이번 요청의 MANUAL 입력 작업을 생성.
    :ivar release_note_id: 원본 릴리즈 노트 ID. 문서가 없으면 None.
    :ivar version_suffix: 확인된 파생 접미사. 값이 없으면 None.
    :ivar released_at: 확인된 출시일. 미확인은 None.
    :ivar version_order: 같은 계열에서 확인된 순서. 미확인은 None.
    :ivar item_mapping_status: Item 매핑 확인 상태. UNKNOWN 또는 VERIFIED.
    :ivar source_reference: 필드별 원본 시트·셀 위치. 직접 입력은 None 허용.
    :ivar edited_fields: 저장 전 검토 과정에서 직접 수정한 필드 이름 튜플.
    """

    family_id: int
    version_raw: str
    batch_id: int | None = None
    release_note_id: int | None = None
    version_suffix: str | None = None
    released_at: date | None = None
    version_order: int | None = None
    item_mapping_status: str = "UNKNOWN"
    source_reference: dict | None = None
    edited_fields: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ItemInput:
    """업데이트 Item 등록 요청 데이터.

    :ivar content: 업데이트 내용.
    :ivar batch_id: 원본 입력 작업 ID. None이면 이번 요청의 MANUAL 입력 작업을 사용.
    :ivar release_note_id: 원본 릴리즈 노트 ID. 문서가 없으면 None.
    :ivar item_no: 원본 Item 번호. 확인되지 않았으면 None.
    :ivar source_reference: 필드별 원본 시트·셀 위치. 직접 입력은 None 허용.
    :ivar edited_fields: 저장 전 검토 과정에서 직접 수정한 필드 이름 튜플.
    """

    content: str
    batch_id: int | None = None
    release_note_id: int | None = None
    item_no: int | None = None
    source_reference: dict | None = None
    edited_fields: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ItemLinkInput:
    """릴리즈별 Item 연결 저장 요청 데이터.

    :ivar item_id: 연결할 기존 Item ID.
    :ivar item_state: 해당 릴리즈의 Item 포함 상태.
    :ivar target_scope: 모델 적용 범위. ALL, LISTED 또는 UNKNOWN.
    :ivar target_group_ids: LISTED일 때 원본 검토로 확인한 적용 모델 ID 튜플.
    """

    item_id: int
    item_state: ItemState
    target_scope: TargetScope = TargetScope.UNKNOWN
    target_group_ids: tuple[int, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SourceCell:
    """원본 셀 조회 결과 데이터.

    :ivar field: 원본 위치에 연결된 관리 항목 이름.
    :ivar sheet: 원본 시트 이름.
    :ivar address: 원본 셀 주소.
    :ivar value: 입력 작업에 보관된 원본 셀 값.
    """

    field: str
    sheet: str
    address: str
    value: object


@dataclass(frozen=True, kw_only=True)
class RecordView:
    """원본·현재 관리값 비교 데이터.

    :ivar table: 조회한 관리 테이블 이름.
    :ivar current: 직접 수정한 값을 포함한 현재 DB 행. edited_fields에 수정 항목을 기록.
    :ivar source_type: 원본 입력 방식. EXCEL 또는 MANUAL.
    :ivar imported_at: 원본 입력 작업이 등록된 시각.
    :ivar original_cells: 원본 시트·셀 주소와 값 목록. 확인할 출처가 없으면 빈 튜플.
    """

    table: str
    current: dict
    source_type: str
    imported_at: datetime
    original_cells: tuple[SourceCell, ...]

    @property
    def has_original(self) -> bool:
        """비교할 수 있는 원본 셀이 존재하는지 확인한다.

        :return: 원본 셀이 하나 이상이면 True, 없으면 False.
        """
        return bool(self.original_cells)


@dataclass(frozen=True, kw_only=True)
class LinkedItemView:
    """릴리즈별 Item 조회 결과 데이터.

    :ivar item: Item의 현재 관리값과 원본 셀 비교 결과.
    :ivar item_state: 이 릴리즈에 저장된 Item 포함 상태.
    :ivar target_scope: 이 릴리즈에 저장된 모델 적용 범위.
    :ivar target_group_ids: 적용 모델 ID 튜플. 지정 목록이 없으면 빈 튜플.
    """

    item: RecordView
    item_state: ItemState
    target_scope: TargetScope
    target_group_ids: tuple[int, ...]


@dataclass(frozen=True, kw_only=True)
class ReleaseDetail:
    """릴리즈 상세 조회 결과 데이터.

    :ivar release: 릴리즈의 현재 관리값과 원본 셀 비교 결과.
    :ivar items: 이 릴리즈에 명시적으로 연결된 모든 Item 목록.
    """

    release: RecordView
    items: tuple[LinkedItemView, ...]
