"""이력 비교에서 저장된 릴리즈의 ID와 공백을 포함한 정확한 원문을 유지한다."""

from collections import Counter
from collections.abc import Iterable

from setron.features.storage.inputs import require
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases, sort_releases


class HistoryReleases(FamilyReleases):
    """정확한 원문을 우선 대응하고, 공백 제거 후 유일한 표기만 보조 대응한다.

    서로 다른 원문이 공백 제거 후 같아져도 ID·원문·릴리즈 순서는 모두 보존한다.
    양쪽 버전이 확인되면 기존 BE-003 순서 판정을 그대로 사용한다.
    """

    def __init__(self, family_id: int, releases: Iterable[ReleaseVersion]):
        values = tuple(releases)
        require(all(release.family_id == family_id for release in values),
                "이력 비교 자료에 다른 계열의 릴리즈가 섞여 있습니다.")
        self.family_id = family_id
        self.releases = values
        self.sorted = sort_releases(values)
        self._by_id = {release.release_version_id: release for release in values}
        self._by_raw = {release.version_raw: release for release in values}
        require(len(self._by_id) == len(values), "이력 비교 릴리즈 ID가 중복되었습니다.")
        require(len(self._by_raw) == len(values), "이력 비교 릴리즈 원문이 중복되었습니다.")
        self._aliases = {}
        counts = Counter(release.version_raw.strip() for release in values)
        self._unique_normalized = {release.version_raw.strip(): release for release in values
                                   if counts[release.version_raw.strip()] == 1}

    def match(self, version_raw: str | None) -> ReleaseVersion | None:
        """정확한 원문 대응을 먼저 찾으며 모호한 공백 정규화로는 연결하지 않는다."""
        if version_raw is None:
            return None
        exact = self._by_raw.get(version_raw)
        if exact is not None:
            return exact
        key = version_raw.strip()
        return self._unique_normalized.get(key) if key else None
