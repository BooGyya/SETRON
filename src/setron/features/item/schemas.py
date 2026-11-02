"""Item 포함 버전과 적용 설비 판정의 입력과 결과 형식 (명세 BE-014).

입력은 ERD `item`(:class:`Item`)과 `release_version_item`(:class:`VersionItem`)이다. 릴리즈 순서는
BE-003의 ``FamilyReleases``, 설비 현황은 BE-012의 ``EquipmentRecord``를 그대로 쓴다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.version.schemas import ReleaseVersion


class ItemState(str, Enum):
    """`release_version_item.item_state`에 저장하는 값."""

    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"


class TargetScope(str, Enum):
    """`release_version_item.target_scope`: 모델 적용 범위."""

    ALL = "ALL"
    LISTED = "LISTED"
    UNKNOWN = "UNKNOWN"


class Application(str, Enum):
    """화면에 보여주는 Item 적용 판정. 저장 상태 세 가지에 '대상 외'를 더한다."""

    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"
    NOT_TARGET = "NOT_TARGET"


APPLICATION_LABELS = {
    Application.APPLIED: "적용",
    Application.NOT_APPLIED: "미적용",
    Application.UNKNOWN: "미확인",
    Application.NOT_TARGET: "대상 외",
}


class Basis(str, Enum):
    """판정 근거."""

    STORED = "STORED"              # 이 버전의 Item 상태가 저장되어 있음
    NOT_STORED = "NOT_STORED"      # 이 계열에 Item이 있지만 이 버전의 상태는 저장되지 않음
    NOT_IN_FAMILY = "NOT_IN_FAMILY"  # 이 계열의 릴리즈에 없는 Item
    NOT_LISTED = "NOT_LISTED"      # 적용 모델 목록에 없음
    NO_RELEASE_DATA = "NO_RELEASE_DATA"
    MISSING_VERSION = "MISSING_VERSION"
    NOT_REGISTERED = "NOT_REGISTERED"
    SCOPE_UNKNOWN = "SCOPE_UNKNOWN"


# 화면에 보여주는 판정 근거 문구. Item 화면(ui/item_data.py)의 문구와 같다.
BASIS_LABELS = {
    Basis.STORED: "저장된 버전별 Item 상태",
    Basis.NOT_STORED: "이 버전의 저장 행 없음",
    Basis.NOT_IN_FAMILY: "이 계열의 대상 Item 아님",
    Basis.NOT_LISTED: "지정 모델 범위 밖",
    Basis.NO_RELEASE_DATA: "릴리즈 자료 없음",
    Basis.MISSING_VERSION: "관측 SW 버전 없음",
    Basis.NOT_REGISTERED: "등록 릴리즈 대응 없음",
    Basis.SCOPE_UNKNOWN: "모델 적용 범위 미확인",
}


@dataclass(frozen=True)
class Item:
    """변경 항목 하나 (ERD `item`)."""

    item_id: int
    item_no: int | None
    content: str


@dataclass(frozen=True)
class VersionItem:
    """버전 하나 × Item 하나의 저장 상태 (ERD `release_version_item`).

    ``target_group_ids``는 ``target_scope``가 LISTED일 때의 적용 모델 ID 목록이다.
    """

    release_version_id: int
    item_id: int
    state: ItemState
    target_scope: TargetScope = TargetScope.UNKNOWN
    target_group_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class VersionItemState:
    """릴리즈 버전 하나에 Item이 포함되는지 (모델 범위 적용 전).

    ``source``는 판정에 쓴 이 버전의 저장 행이며, 저장 행이 없으면 None이다.
    """

    release: ReleaseVersion
    state: ItemState
    basis: Basis
    source: VersionItem | None = None

    @property
    def basis_label(self) -> str:
        return BASIS_LABELS[self.basis]


@dataclass(frozen=True)
class PmApplication:
    pm: PmVersion
    release: ReleaseVersion | None
    application: Application
    basis: Basis

    @property
    def label(self) -> str:
        return APPLICATION_LABELS[self.application]

    @property
    def basis_label(self) -> str:
        return BASIS_LABELS[self.basis]


@dataclass(frozen=True)
class EquipmentApplication:
    """설비 한 대의 Item 적용 판정. PM별 판정은 ``pms``에 그대로 남긴다."""

    record: EquipmentRecord
    application: Application
    pms: tuple[PmApplication, ...]

    @property
    def label(self) -> str:
        return APPLICATION_LABELS[self.application]


@dataclass(frozen=True)
class ItemSummary:
    """선택한 Item의 설비별 적용 현황. Item 히트맵(사업장별 분포)에 쓴다. HPA는 뺀다."""

    item: Item
    equipment: tuple[EquipmentApplication, ...]
    hpa_excluded: int = 0

    def counts(self, site: str | None = None) -> dict[Application, int]:
        """판정별 설비 수. ``site``를 주면 그 사업장만 센다. 없는 판정은 0으로 채운다."""
        counter = Counter(
            e.application for e in self.equipment if site is None or e.record.site == site
        )
        return {a: counter.get(a, 0) for a in Application}

    @property
    def sites(self) -> tuple[str, ...]:
        return tuple(sorted({e.record.site for e in self.equipment}))
