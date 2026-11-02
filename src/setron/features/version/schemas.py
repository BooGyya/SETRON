"""버전 비교와 N 대비 단계 계산에 쓰는 데이터 형식 (명세 BE-003).

입력은 한 버전 계열의 릴리즈 목록(:class:`ReleaseVersion`)이고, 결과는 설비에 적용된 버전 하나를
목표 N과 비교한 :class:`VersionComparison`이다. 릴리즈 목록은 ERD `release_version` 행에서 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


@dataclass(frozen=True)
class ReleaseVersion:
    """같은 계열의 릴리즈 버전 하나 (ERD `release_version`).

    ``version_order``는 계열 안에서 확인된 순서이며 클수록 나중 버전이다. 모르면 None이다.
    ``released_at``은 출시일이며 노후도 계산(BE-016)에 쓴다. 모르면 None이다.
    """

    release_version_id: int
    family_id: int
    version_raw: str
    version_order: int | None = None
    released_at: date | None = None


@dataclass(frozen=True)
class SortedReleases:
    """계열의 릴리즈를 순서대로 나눈 결과.

    ``ordered``는 순서가 확인된 버전(오래된 것부터), ``unordered``는 순서를 모르는 버전이다.
    ``duplicated_orders``는 여러 버전에 같은 순서 값이 들어간 경우로, 해당 버전은 서로의 선후를 알 수 없다.
    """

    ordered: tuple[ReleaseVersion, ...]
    unordered: tuple[ReleaseVersion, ...]
    duplicated_orders: tuple[int, ...]


class NotationDifference(str, Enum):
    """현황표 원문과 릴리즈 원문의 표기 차이. 후보를 보여줄 때만 쓰며 자동으로 연결하지 않는다."""

    SITE_MARK = "SITE_MARK"      # (H) 같은 사업장 표식
    WHITESPACE = "WHITESPACE"    # 공백 (예: "+ Hotfix" ↔ "+Hotfix")
    CASE = "CASE"                # 대소문자
    PREFIX = "PREFIX"            # 앞의 v 유무
    SEPARATOR = "SEPARATOR"      # 점·밑줄·대시 (예: v999.3.7 ↔ v999.3_7)


DIFFERENCE_LABELS = {
    NotationDifference.SITE_MARK: "사업장 표식",
    NotationDifference.WHITESPACE: "공백",
    NotationDifference.CASE: "대소문자",
    NotationDifference.PREFIX: "앞의 v",
    NotationDifference.SEPARATOR: "점·밑줄·대시",
}


@dataclass(frozen=True)
class ReleaseCandidate:
    """표기만 다른 것으로 보이는 릴리즈 후보. 사용자가 고르면 그 릴리즈 ID를 대응 릴리즈로 저장한다."""

    release: ReleaseVersion
    differences: tuple[NotationDifference, ...]

    @property
    def description(self) -> str:
        return ", ".join(DIFFERENCE_LABELS[d] for d in self.differences) + " 차이"


class CompareStatus(str, Enum):
    AT_OR_ABOVE = "AT_OR_ABOVE"
    BELOW = "BELOW"
    UNDETERMINED = "UNDETERMINED"


class CompareReason(str, Enum):
    """판단 불가이거나 N보다 몇 단계 낮은지 모르는 이유."""

    MISSING_VERSION = "MISSING_VERSION"
    NOT_REGISTERED = "NOT_REGISTERED"
    OTHER_FAMILY = "OTHER_FAMILY"
    ORDER_UNKNOWN = "ORDER_UNKNOWN"
    ORDER_CONFLICT = "ORDER_CONFLICT"
    TARGET_NOT_SET = "TARGET_NOT_SET"
    TARGET_INVALID = "TARGET_INVALID"
    NO_RELEASE_DATA = "NO_RELEASE_DATA"


REASON_MESSAGES = {
    CompareReason.MISSING_VERSION: "버전 값이 비어 있습니다.",
    CompareReason.NOT_REGISTERED: "릴리즈 노트에 없는 버전이라 순서를 알 수 없습니다.",
    CompareReason.OTHER_FAMILY: "다른 버전 계열의 릴리즈라 비교하지 않습니다.",
    CompareReason.ORDER_UNKNOWN: "릴리즈 순서가 확인되지 않은 버전이 있습니다.",
    CompareReason.ORDER_CONFLICT: "같은 순서 값을 가진 릴리즈가 있어 선후를 알 수 없습니다.",
    CompareReason.TARGET_NOT_SET: "이 계열의 목표 N이 지정되지 않았습니다.",
    CompareReason.TARGET_INVALID: "목표 N을 이 계열의 순서가 확인된 릴리즈에서 찾을 수 없습니다. N을 다시 선택해 주세요.",
    CompareReason.NO_RELEASE_DATA: "이 계열의 릴리즈 자료가 없어 비교할 수 없습니다.",
}


@dataclass(frozen=True)
class VersionComparison:
    """설비에 적용된 버전 하나를 목표 N과 비교한 결과.

    - ``version_raw``: 설비 현황의 원문. 표기를 바꾸지 않는다.
    - ``release``: 대응 릴리즈. None이면 릴리즈 미등록이거나 버전이 비어 있다.
    - ``steps_below``: N보다 몇 단계 낮은지. BELOW이지만 단계를 확정할 수 없으면 None이다.
    - ``reason``: UNDETERMINED의 이유, 또는 BELOW인데 단계를 모르는 이유.
    """

    version_raw: str | None
    release: ReleaseVersion | None
    target: ReleaseVersion | None
    status: CompareStatus
    steps_below: int | None = None
    reason: CompareReason | None = None

    @property
    def registered(self) -> bool:
        return self.release is not None

    @property
    def label(self) -> str:
        """히트맵 단계 표시용 문구: 'N 이상', 'N-2', 'N 미만', '판단 불가'."""
        if self.status is CompareStatus.AT_OR_ABOVE:
            return "N 이상"
        if self.status is CompareStatus.BELOW:
            return f"N-{self.steps_below}" if self.steps_below is not None else "N 미만"
        return "판단 불가"

    @property
    def message(self) -> str | None:
        return REASON_MESSAGES[self.reason] if self.reason else None
