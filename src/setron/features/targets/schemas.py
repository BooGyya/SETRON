"""모델·계열 적용 근거와 영구 저장하지 않는 목표 선택 결과."""

from dataclasses import dataclass
from typing import Any

from setron.features.version.schemas import ReleaseVersion


@dataclass(frozen=True)
class CandidateConfirmation:
    """사용자가 확인한 적용 표식·분기/순서 근거와 원본 출처.

    source_text 또는 필드별 source_reference가 필요하다. 이 값은 모델 적용
    가능성을 명시적으로 확인한 근거이며 공유 문서나 계열 관리 모델로 추론하지 않는다.
    """

    release_id: int
    applicability_basis: str
    order_basis: str
    source_text: str | None = None
    source_reference: dict[str, Any] | None = None


@dataclass(frozen=True)
class TargetCandidate:
    """현재 릴리즈 및 저장 시점의 확인 근거가 모두 유효한 N 후보."""

    release: ReleaseVersion
    confirmation: CandidateConfirmation

    @property
    def release_id(self):
        return self.release.release_version_id


@dataclass(frozen=True)
class ExcludedCandidate:
    release_id: int
    reason: str


@dataclass(frozen=True)
class TargetPair:
    """한 모델·계열의 후보와 요청 시점 선택. 잘못된 선택 ID도 그대로 보존한다."""

    model_id: int
    family_id: int
    candidates: tuple[TargetCandidate, ...]
    recommended_release_id: int | None
    selected_release_id: int | None
    selection_valid: bool
    reason: str | None
    excluded: tuple[ExcludedCandidate, ...] = ()
    applicability_confirmed: bool = False

    @property
    def key(self):
        return self.model_id, self.family_id


@dataclass(frozen=True)
class TargetCatalog:
    pairs: tuple[TargetPair, ...]

    @property
    def applicability(self):
        """BE-010에 넘길 현재 검증된 적용 가능 ID. 미확인 쌍은 키도 만들지 않는다."""
        return {pair.key: tuple(c.release_id for c in pair.candidates)
                for pair in self.pairs if pair.applicability_confirmed}

    @property
    def targets(self):
        """선택한 N 원본. 무효 ID·명시적 None도 보존하며 DB에 저장하지 않는다."""
        return {pair.key: pair.selected_release_id for pair in self.pairs}

    @property
    def effective_targets(self):
        return {pair.key: pair.selected_release_id for pair in self.pairs if pair.selection_valid}
