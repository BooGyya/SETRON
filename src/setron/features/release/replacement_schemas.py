"""계열 전체 교체의 검토 요청과 결과 형식 (BE-010)."""

from dataclasses import dataclass, field
from typing import Any

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.storage.validation import StorageValidationError

from .schemas import ItemInput, ReleaseInput


@dataclass(frozen=True, kw_only=True)
class ReplacementItem:
    """교체할 Item 데이터.

    ivar
    - key: 요청 안에서만 사용하는 고유 키. 여러 릴리즈가 같은 Item을 참조할 수 있음.
    - value: 새 원본에 대응하는 검토한 Item 값. batch_id는 지정하지 않음.
    """

    key: str
    value: ItemInput


@dataclass(frozen=True, kw_only=True)
class ReplacementLink:
    """검토한 버전별 Item 포함 관계 데이터.

    ivar
    - item_key: 요청 안의 Item 키.
    - item_state: 누적 포함·제외·분기 검토를 마친 상태. 순서만으로 상속하지 않음.
    - target_scope: 확인한 모델 범위.
    - target_group_ids: LISTED 범위의 모델 ID 튜플.
    """

    item_key: str
    item_state: ItemState = ItemState.APPLIED
    target_scope: TargetScope = TargetScope.UNKNOWN
    target_group_ids: tuple[int, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ReplacementRelease:
    """교체할 릴리즈 데이터.

    ivar
    - key: 요청 안의 고유 릴리즈 키.
    - value: 새 원본에 대응하는 릴리즈 값. batch_id는 지정하지 않음.
    - links: 검토한 모든 Item 연결. 빈 튜플은 포함 Item이 없다는 명시적 결과.
    """

    key: str
    value: ReleaseInput
    links: tuple[ReplacementLink, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ReplacementRequest:
    """한 계열의 누적 노트 전체 교체 요청 데이터.

    ivar
    - family_id: 다른 계열과 분리해서 교체할 계열 ID.
    - releases: 새 자료의 전체 릴리즈. 빈 목록은 확인한 계열 전체 비우기.
    - items: 새 자료의 전체 Item. 연결되지 않은 Item은 허용하지 않음.
    - raw_data: 새 입력 작업에 보관할 시트명별 셀 주소·원본값 객체.
    - complete_note_confirmed: 호출자가 전체 누적 노트와 포함·제외·분기를 검토했는지 여부.
    - filename_confirmed: 호출자가 저장 전 파일명을 확인했는지 여부. 파일명은 DB에 저장하지 않음.
    """

    family_id: int
    releases: tuple[ReplacementRelease, ...]
    items: tuple[ReplacementItem, ...]
    raw_data: dict
    complete_note_confirmed: bool = False
    filename_confirmed: bool = False


@dataclass(frozen=True)
class MatchQuestion:
    """같은 항목인지 확인할 매칭 후보 데이터.

    ivar
    - kind: release 또는 item.
    - key: 새 요청의 항목 키.
    - candidates: 현재 계열의 후보 ID 목록. None 선택은 별개의 새 항목임을 뜻함.
    - reason: 자동 확정할 수 없는 이유.
    """

    kind: str
    key: str
    candidates: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class ValueConflict:
    """현재 관리값과 새 값의 충돌 데이터.

    ivar
    - kind: release 또는 item.
    - key: 새 요청의 항목 키.
    - field: 선택해야 할 필드 이름.
    - current: 현재 관리값.
    - incoming: 새 자료에서 검토한 값.
    """

    kind: str
    key: str
    field: str
    current: Any
    incoming: Any


@dataclass(frozen=True)
class LinkConflict:
    """같은 릴리즈·Item 연결의 포함 상태와 적용 범위 충돌 데이터.

    ivar
    - release_key, item_key: 새 요청 안에서 연결을 구분하는 두 키.
    - current: 현재 저장된 포함 상태·모델 범위의 전체 묶음.
    - incoming: 새 노트에서 검토한 포함 상태·모델 범위의 전체 묶음.
    """

    release_key: str
    item_key: str
    current: ReplacementLink
    incoming: ReplacementLink


@dataclass(frozen=True)
class RiskReconnection:
    """저장 전에 확인할 수 있는 수동 위험의 재연결 근거와 대상 키.

    ivar
    - risk_rule_id: 보존하는 기존 수동 위험 ID.
    - target_reference: 기존 사용자 근거와 삭제 전 원본·소속을 합친 복사본.
    - proposed_targets: 대상 컬럼별 새 릴리즈·Item 요청 키. 미확인 또는 미지정은 None.
    - can_restore: 필수 대상·원래 알려진 해제 경계·확인된 순서가 모두 유효한지 여부.
    - requires_confirmation: 이전 연결 미확인 또는 버전 원문 변경으로 명시 확인이 필요한지 여부.
    - reason: 확인할 내용 또는 복원할 수 없는 이유. 추가 확인이 없으면 None.
    """

    risk_rule_id: int
    target_reference: dict
    proposed_targets: dict[str, str | None]
    can_restore: bool
    requires_confirmation: bool
    reason: str | None = None


@dataclass(frozen=True)
class ReplacementPreview:
    """쓰기 없이 생성한 전체 교체 검토 결과 데이터.

    ivar
    - fingerprint: DB와 요청 내용의 지문. 저장 전에 같은 상태인지 재검증.
    - release_matches: 확정한 새 키별 기존 릴리즈 ID 또는 신규 None.
    - item_matches: 확정한 새 키별 기존 Item ID 또는 신규 None.
    - questions: 명시적인 매칭 선택이 필요한 항목.
    - conflicts: CURRENT 또는 INCOMING 선택이 필요한 값.
    - risk_reconnections: 저장 전에 검토할 수동 위험의 복원 후보·근거·확인 필요 여부.
    - link_conflicts: 사용자 선택이 남은 버전별 Item 포함 상태·모델 범위의 충돌.
    """

    fingerprint: str
    release_matches: dict[str, int | None]
    item_matches: dict[str, int | None]
    questions: tuple[MatchQuestion, ...]
    conflicts: tuple[ValueConflict, ...]
    risk_reconnections: tuple[RiskReconnection, ...] = ()
    link_conflicts: tuple[LinkConflict, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ReplacementReview:
    """사용자의 매칭·값 선택 데이터.

    ivar
    - fingerprint: 최초 검토 시 받은 지문. 선택 변경 후에도 같은 DB·요청에만 유효.
    - release_matches: 불확실한 새 키에 대한 기존 ID 또는 별개 신규 None 선택.
    - item_matches: 불확실한 Item의 기존 ID 또는 별개 신규 None 선택.
    - choices: (kind, key, field)별 CURRENT 또는 INCOMING 선택.
    - risk_confirmations: 복원 후보를 검토하고 재연결을 확인한 기존 미확인 위험 ID 튜플.
    - link_choices: (릴리즈 키, Item 키)별 CURRENT 또는 INCOMING 선택. 포함 상태와
      모델 범위·모델 ID를 한 묶음으로 선택해 서로 모순되는 범위를 만들지 않음.
    """

    fingerprint: str
    release_matches: dict[str, int | None] = field(default_factory=dict)
    item_matches: dict[str, int | None] = field(default_factory=dict)
    choices: dict[tuple[str, str, str], str] = field(default_factory=dict)
    risk_confirmations: tuple[int, ...] = ()
    link_choices: dict[tuple[str, str], str] = field(default_factory=dict)


class ReplacementReviewRequired(StorageValidationError):
    """교체 검토 미완료 오류.

    ivar
    - preview: 호출 화면에 표시할 최신 매칭 질문과 값 충돌.
    """

    def __init__(self, preview):
        """최신 검토 내용을 보관한다.

        param
        - preview: 선택이 완료되지 않은 ReplacementPreview.
        """
        super().__init__("매칭과 수정값 충돌을 확인한 뒤 다시 저장해 주세요.")
        self.preview = preview
