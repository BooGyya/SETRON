"""같은 버전 계열 안에서 버전을 비교하고 목표 N보다 몇 단계 낮은지 계산한다 (명세 BE-003).

순서의 근거는 릴리즈 자료에서 확인한 ``version_order``뿐이다. 버전 문자열의 자릿수나 접미사
(``_n4``, ``_d5``, ``a1``, ``HF1`` 등)로 순서를 추정하지 않는다. 핫픽스도 릴리즈 목록에 순서와 함께
있으면 정규 버전과 같은 순서 안에서 비교한다. 순서를 확인할 수 없으면 '판단 불가'로 돌려준다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping

from setron.features.version.schemas import (
    CompareReason,
    CompareStatus,
    NotationDifference,
    ReleaseCandidate,
    ReleaseVersion,
    SortedReleases,
    VersionComparison,
)


def sort_releases(releases: Iterable[ReleaseVersion]) -> SortedReleases:
    """릴리즈를 확인된 순서대로 정렬하고, 순서를 모르는 버전과 순서 값이 겹치는 경우를 따로 알린다."""
    releases = tuple(releases)
    ordered = sorted(
        (r for r in releases if r.version_order is not None),
        key=lambda r: (r.version_order, r.release_version_id),
    )
    counts = Counter(r.version_order for r in ordered)
    return SortedReleases(
        ordered=tuple(ordered),
        unordered=tuple(r for r in releases if r.version_order is None),
        duplicated_orders=tuple(sorted(order for order, n in counts.items() if n > 1)),
    )


class FamilyReleases:
    """한 버전 계열의 릴리즈 목록. 한 번 만들어 같은 계열의 여러 설비 버전 비교에 다시 쓴다.

    ``aliases``는 같은 버전으로 확인된 다른 표기를 릴리즈 원문에 연결한다
    (예: ``{"v999.3_7": "v999.3.7"}``). 확인된 대응만 넣으며, 점·밑줄을 일괄 치환하지 않는다.
    ``exact_raw=True``인 DB 원본 조회에서는 앞뒤 공백도 원문의 일부로 보존해 대응한다.
    """

    def __init__(
        self,
        family_id: int,
        releases: Iterable[ReleaseVersion],
        aliases: Mapping[str, str] | None = None,
        *,
        exact_raw: bool = False,
    ) -> None:
        releases = tuple(releases)
        others = [r.version_raw for r in releases if r.family_id != family_id]
        if others:
            raise ValueError(f"계열 {family_id}가 아닌 릴리즈가 섞여 있습니다: {others}")

        self.family_id = family_id
        self._exact_raw = exact_raw
        self.releases = releases
        self.sorted = sort_releases(releases)
        self._by_id = {r.release_version_id: r for r in releases}
        self._by_raw: dict[str, ReleaseVersion] = {}
        for release in releases:
            key = release.version_raw if exact_raw else release.version_raw.strip()
            if key in self._by_raw:
                raise ValueError(f"계열 {family_id}에 같은 원문의 릴리즈가 두 개 있습니다: {key}")
            self._by_raw[key] = release
        self._aliases: dict[str, ReleaseVersion] = {}
        for alias, raw in (aliases or {}).items():
            target = raw if exact_raw else raw.strip()
            if target not in self._by_raw:
                raise ValueError(f"대응 표기 {alias}가 가리키는 릴리즈 {raw}가 계열 {family_id}에 없습니다.")
            self._aliases[alias if exact_raw else alias.strip()] = self._by_raw[target]

    def get(self, release_version_id: int) -> ReleaseVersion | None:
        return self._by_id.get(release_version_id)

    def match(self, version_raw: str | None) -> ReleaseVersion | None:
        """설비 현황의 버전 원문에 대응하는 릴리즈를 찾는다. 원문 일치 또는 확인된 대응 표기만 연결한다."""
        key = (version_raw or "") if self._exact_raw else (version_raw or "").strip()
        if not key:
            return None
        return self._by_raw.get(key) or self._aliases.get(key)

    def candidates(self, version_raw: str | None) -> tuple[ReleaseCandidate, ...]:
        """원문과 표기만 다른 것으로 보이는 릴리즈 후보를 찾는다. 자동으로 연결하지 않는다.

        공백·대소문자·앞의 v·(H) 표식·점과 밑줄의 차이만 무시하고 비교한다. 원문이 바로 연결되면
        (:meth:`match`) 후보를 내지 않는다. 차이가 적은 후보부터, 점·밑줄 차이가 있는 후보는 뒤에 둔다.
        """
        if not (version_raw or "").strip() or self.match(version_raw) is not None:
            return ()
        key = _notation_key(version_raw)
        found = []
        for release in self.releases:
            if _notation_key(release.version_raw) != key:
                continue
            differences = tuple(
                d for d in NotationDifference
                if _notation_key(version_raw, skip=d) != _notation_key(release.version_raw, skip=d)
            )
            found.append(ReleaseCandidate(release, differences))
        return tuple(sorted(found, key=lambda c: (
            NotationDifference.SEPARATOR in c.differences,
            len(c.differences),
            c.release.version_order is None,
            c.release.version_order or 0,
        )))


_SITE_MARK = re.compile(r"\s*\(\s*h\s*\)\s*", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_SEPARATORS = str.maketrans({"_": ".", "-": "."})


def _notation_key(text: str, skip: NotationDifference | None = None) -> str:
    """표기 차이를 지운 비교용 문자열. ``skip``에 준 차이는 지우지 않는다 (어떤 차이가 있었는지 찾을 때 쓴다)."""
    D = NotationDifference
    if skip is not D.SITE_MARK:
        text = _SITE_MARK.sub("", text)
    if skip is not D.WHITESPACE:
        text = _WHITESPACE.sub("", text)
    if skip is not D.CASE:
        text = text.casefold()
    if skip is not D.PREFIX and text[:1] == "v":
        text = text[1:]
    if skip is not D.SEPARATOR:
        text = text.translate(_SEPARATORS)
    return text


def compare_version(
    family: FamilyReleases,
    version_raw: str | None,
    target_id: int | None,
    *,
    release_version_id: int | None = None,
) -> VersionComparison:
    """설비에 적용된 버전 하나를 목표 N과 비교한다.

    ``release_version_id``는 저장된 대응 릴리즈(ERD `equipment_version.release_version_id`)다.
    없으면 ``version_raw``로 릴리즈를 찾는다. PM마다 버전이 다르면 PM별로 각각 호출하며,
    계열 전체의 부합·미달 판단(BE-012)은 이 결과들을 모아서 한다.

    param
    - family: 같은 계열의 현재 릴리즈와 확정 별칭.
    - version_raw: 설비 현황 원문. 비어 있으면 버전 누락으로 처리.
    - target_id: 모델·계열에 지정한 N의 ID. None이면 목표 미지정.
    - release_version_id: 확인된 대응 릴리즈 ID. 잘못된 ID를 원문으로 대신 연결하지 않음.

    return
    - N 대비 판정. N이 없거나 무효여도 확인한 설치 버전의 등록 정보는 유지.
    """
    duplicated = set(family.sorted.duplicated_orders)

    def result(status, release=None, target=None, steps=None, reason=None):
        return VersionComparison(version_raw, release, target, status, steps, reason)

    if not family.releases:
        return result(CompareStatus.UNDETERMINED, reason=CompareReason.NO_RELEASE_DATA)
    # 버전 등록 여부는 목표 N의 유무·유효성과 독립적이다.
    release = (family.get(release_version_id) if release_version_id is not None
               else family.match(version_raw) if (version_raw or "").strip() else None)
    if target_id is None:
        return result(CompareStatus.UNDETERMINED, release=release, reason=CompareReason.TARGET_NOT_SET)
    target = family.get(target_id)
    if target is None or target.version_order is None or target.version_order in duplicated:
        return result(CompareStatus.UNDETERMINED, release=release, target=target, reason=CompareReason.TARGET_INVALID)

    if release_version_id is not None:
        if release is None:
            return result(CompareStatus.UNDETERMINED, target=target, reason=CompareReason.OTHER_FAMILY)
    elif not (version_raw or "").strip():
        return result(CompareStatus.UNDETERMINED, target=target, reason=CompareReason.MISSING_VERSION)
    else:
        if release is None:
            return result(CompareStatus.UNDETERMINED, target=target, reason=CompareReason.NOT_REGISTERED)

    if release.release_version_id == target.release_version_id:
        return result(CompareStatus.AT_OR_ABOVE, release, target)
    if release.version_order is None:
        return result(CompareStatus.UNDETERMINED, release, target, reason=CompareReason.ORDER_UNKNOWN)
    if release.version_order in duplicated:
        return result(CompareStatus.UNDETERMINED, release, target, reason=CompareReason.ORDER_CONFLICT)
    if release.version_order > target.version_order:
        return result(CompareStatus.AT_OR_ABOVE, release, target)

    # N 미만은 확정이다. 단계 수는 사이에 있는 릴리즈를 모두 알 때만 센다.
    between = {
        r.version_order
        for r in family.sorted.ordered
        if release.version_order <= r.version_order < target.version_order
    }
    if between & duplicated:
        return result(CompareStatus.BELOW, release, target, reason=CompareReason.ORDER_CONFLICT)
    if family.sorted.unordered:
        return result(CompareStatus.BELOW, release, target, reason=CompareReason.ORDER_UNKNOWN)
    return result(CompareStatus.BELOW, release, target, steps=len(between))
