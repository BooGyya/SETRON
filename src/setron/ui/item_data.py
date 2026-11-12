"""B-3 읽기 계약. 한 관측일의 자료만 받고 기존 Item 업무 판정을 재사용한다."""

from dataclasses import dataclass
from datetime import date

from setron.features.aggregation.schemas import EquipmentRecord
from setron.features.item.schemas import Application, BASIS_LABELS, TargetScope
from setron.features.item.service import ItemCatalog, search_items
from setron.ui.item_original import jira_by_item
from setron.ui.version_data import VersionCatalog


@dataclass(frozen=True)
class ItemManagerData:
    """관측 연결 상태와 출처 설명을 구분하는 Item 화면 자료.

    ivar
    - versions: 릴리즈·Item 읽기 자료.
    - records: 같은 기준일의 설비 관측. 정상 0건은 빈 튜플.
    - base_date: 연결된 관측 기준일. 미연결이면 None 가능.
    - observations_connected: 관측 자료 전달 여부. 설명 문자열로 추정하지 않는다.
    - observation_label: 관측 자료 출처 설명. 생략은 연결 상태를 뜻하지 않는다.
    """

    versions: VersionCatalog
    records: tuple[EquipmentRecord, ...] = ()
    base_date: date | None = None
    observations_connected: bool = False
    observation_label: str = "관측 자료 출처 설명 미제공"
    risk_rules: tuple = ()

    def __post_init__(self):
        if not isinstance(self.versions, VersionCatalog):
            raise ValueError("Item 릴리즈 자료 형식이 올바르지 않습니다.")
        if self.records and not self.observations_connected:
            raise ValueError("미연결 관측 자료를 사용할 수 없습니다.")
        if self.observations_connected and self.base_date is None:
            raise ValueError("관측 기준일이 필요합니다.")
        # 설비별 최신 관측이라 관측일은 섞일 수 있지만, 기준일 이후 관측은 섞지 않는다 (QA Q-01).
        if any(r.base_date > self.base_date for r in self.records):
            raise ValueError("기준일 이후 관측은 합칠 수 없습니다.")
        if len({r.equipment_id for r in self.records}) != len(self.records):
            raise ValueError("같은 설비 ID가 중복되었습니다.")

    def search(self, query="", *, family_id=None):
        entries = self.versions.items
        if family_id is not None:
            releases = {e.release.release_version_id for e in self.versions.releases if e.release.family_id == family_id}
            linked = {r.item_id for r in self.versions.version_items if r.release_version_id in releases}
            entries = tuple(e for e in entries if e.item.item_id in linked)
        items = tuple(e.item for e in entries)
        found = search_items(items, query, match_words=True) if query.strip() else sorted(
            items, key=lambda i: (i.item_no is None, i.item_no or 0, i.item_id))
        by_id = {e.item.item_id: e for e in entries}
        result = [by_id[i.item_id] for i in found]
        needle = "".join(query.split()).casefold()
        if needle and self.versions.original_rows:
            # 릴리즈노트 원본에 적힌 Jira 코드로도 찾는다. 원본에 없는 코드는 만들지 않는다.
            matched = {e.item.item_id for e in result}
            jira = jira_by_item(self.versions.original_rows, by_id)
            result.extend(e for e in entries if e.item.item_id not in matched
                          and any(needle in code.casefold() for code in jira[e.item.item_id].codes))
        return tuple(result)

    def jira(self, item_ids=None):
        """Item별 원본 Jira 코드. 보관 원본이 없으면 미확인으로 돌려준다."""
        ids = [e.item.item_id for e in self.versions.items] if item_ids is None else item_ids
        return jira_by_item(self.versions.original_rows, ids)

    def catalog(self):
        return ItemCatalog(self.versions.families(), self.versions.version_items)

    def scope_label(self, row):
        if row is None or row.target_scope is TargetScope.UNKNOWN:
            return "미확인"
        if row.target_scope is TargetScope.ALL:
            return "전체 모델 (해당 계열)"
        names = {p.model_id: p.model_name for p in self.versions.pairs}
        return ", ".join(names[mid] for mid in row.target_group_ids) or "지정 모델 없음"


def _source_rules(source):
    """해제된 기준까지 포함한 위험 기준. 전체 목록이 없는 자료원은 판정기(활성 기준)만 쓴다."""
    rules = getattr(source, "all_risk_rules", None)
    return tuple(rules if rules is not None else getattr(getattr(source, "assessor", None), "rules", ()))


def item_data_from_source(versions, source):
    """대시보드와 같은 설비별 최신 관측을 쓴다 (QA Q-01). 표시 기준일은 최신 분석 관측일이다."""
    if not source.dates:
        return ItemManagerData(versions, observation_label=source.source_label + " · 관측 기준일 없음",
                               risk_rules=_source_rules(source))
    day = (source.analysis_dates or source.dates)[-1]
    return ItemManagerData(versions, source.snapshot_records(day),
                           day, True, source.source_label, _source_rules(source))


@dataclass(frozen=True)
class ItemEquipmentSelection:
    """Item 화면의 자료 세대·표시 범위를 고정한 설비 이동 요청. A 조회를 대체하지 않는다."""

    data: ItemManagerData
    revision: int
    item_id: int
    equipment_id: int
    site: str | None
    state: Application | None


def selected_application(request):
    """요청의 날짜·설비·Item 연결과 표시 범위를 검증한다. UI 세대 검증은 호출자가 한다."""
    data = request.data
    if not data.observations_connected:
        return None
    catalog = data.catalog()
    if not any(e.item.item_id == request.item_id for e in data.versions.items) or not catalog.item_versions(request.item_id):
        return None
    record = next((r for r in data.records if r.equipment_id == request.equipment_id and not r.is_hpa), None)
    if record is None or (request.site is not None and record.site != request.site):
        return None
    result = catalog.equipment_application(request.item_id, record)
    return result if request.state is None or result.application == request.state else None


def item_evidence_rows(request, application):
    """선택 Item과 PM이 실제로 참조하는 행·출처만 반환한다."""
    data = request.data
    entry = next(e for e in data.versions.items if e.item.item_id == request.item_id)
    rows = [(f"Item {request.item_id}", entry.item.content, "Item 원문", "")]

    def sources(target, references):
        if not references:
            rows.append((target, "출처 미확인", "문서 미확인", "원본 위치 미확인"))
        for ref in references:
            rows.append((f"{target} / {ref.field}", f"입력 작업 {ref.batch_id if ref.batch_id is not None else '미확인'}",
                         f"{ref.document or '문서 미확인'} · 리비전 {ref.revision or '미확인'}",
                         f"{ref.sheet or '시트 미확인'} · {ref.cells or '셀 미확인'}"))

    sources(f"Item {request.item_id}", entry.sources)
    catalog, seen = data.catalog(), set()
    for pm in application.pms:
        release = pm.release
        if release is None or release.release_version_id in seen:
            continue
        seen.add(release.release_version_id)
        state = catalog.version_state(request.item_id, release)
        rows.append((f"릴리즈 ID {release.release_version_id}", release.version_raw,
                     BASIS_LABELS[state.basis] + (f" · 근거 릴리즈 ID {state.source.release_version_id}" if state.source else ""),
                     "모델 범위: " + data.scope_label(state.source)))
        sources(f"릴리즈 {release.release_version_id}", data.versions.entry(release.release_version_id).sources)
    return tuple(rows)
