"""VER5 관리 화면의 독립 작업본. 원본·공통 N·DB를 직접 변경하지 않는다."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
from uuid import uuid4

from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.features.version.schemas import ReleaseVersion
from setron.ui.version_data import ItemEntry, ReleaseEntry, SourceReference


@dataclass(frozen=True)
class VersionChange:
    identity: str
    category: str
    action: str
    target: str
    before: tuple
    after: tuple
    reason: str
    sources: tuple = ()
    recorded_at: str = ""
    persisted: bool = False

    @property
    def differences(self):
        old, new = dict(self.before), dict(self.after)
        return tuple((key, old.get(key), new.get(key)) for key in dict.fromkeys((*old, *new))
                     if old.get(key) != new.get(key))


@dataclass(frozen=True)
class VersionArchive:
    archive_id: str
    title: str
    changes: tuple[VersionChange, ...]
    created_at: str


class VersionWorkspace:
    """화면 입력과 확인한 변경·보관 이력의 불변 사본을 분리한다."""

    def __init__(self):
        self.pending = {}
        self.history = ()
        self.archives = ()
        self.release_overlays = {}
        self.item_overlays = {}
        self.equipment_drafts = {}
        self.software_drafts = {}
        self.metadata = {}
        self.generation = 0

    def stage(self, category, action, target, before, after, reason, *, identity=None, sources=()):
        if category not in {"릴리즈", "Item", "설비 기록", "설비 정보", "Excel"}:
            raise ValueError("변경 종류를 확인해 주세요.")
        if not reason.strip():
            raise ValueError("등록·수정 사유를 입력해 주세요.")
        change = VersionChange(identity or str(uuid4()), category, action, target,
                               tuple(deepcopy(before).items()), tuple(deepcopy(after).items()),
                               reason.strip(), tuple(deepcopy(sources)))
        if not change.differences:
            raise ValueError("변경된 항목이 없습니다.")
        self.pending[change.identity] = change
        self.generation += 1
        return change

    def discard(self, identity):
        self.pending.pop(identity, None)
        self.generation += 1

    def finish(self, identities, *, persisted=False):
        """성공한 변경만 확정. DB 저장 실패/미확인일 때는 호출하지 않는다."""
        changes = tuple(self.pending[key] for key in identities)
        now = datetime.now().isoformat(timespec="seconds")
        saved = tuple(replace(change, recorded_at=now, persisted=persisted) for change in changes)
        for change in changes:
            self.pending.pop(change.identity)
        self.history += saved
        self.generation += 1
        return saved

    def archive(self, changes, *, confirmed=False):
        if not confirmed:
            raise ValueError("보관할 변경 범위를 먼저 확인해 주세요.")
        changes = tuple(changes)
        if not changes or any(change not in self.history for change in changes):
            raise ValueError("저장한 변경 이력에서 보관할 범위를 선택해 주세요.")
        entry = VersionArchive(str(uuid4()), f"보관 작업 R{len(self.archives) + 1}", deepcopy(changes),
                               datetime.now().isoformat(timespec="seconds"))
        self.archives += (entry,)
        return entry

    def catalog(self, base):
        if base is None:
            return None
        releases = {entry.release.release_version_id: entry for entry in base.releases}
        releases.update(self.release_overlays)
        items = {entry.item.item_id: entry for entry in base.items}
        items.update({value[0].item.item_id: value[0] for value in self.item_overlays.values()})
        links = {(row.release_version_id, row.item_id): row for row in base.version_items}
        for _, row in self.item_overlays.values():
            links[row.release_version_id, row.item_id] = row
        return replace(base, releases=tuple(releases.values()), items=tuple(items.values()),
                       version_items=tuple(links.values()))


def release_values(entry, metadata=None):
    release = entry.release
    return dict(version_raw=release.version_raw, released_at=release.released_at,
                version_order=release.version_order, order_basis=(metadata or {}).get("order_basis", ""),
                applicability=(metadata or {}).get("applicability", "적용 대상 확인 필요"))


def make_release(catalog, model_id, family_id, values, *, entry=None):
    raw = values.get("version_raw", "")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("버전 원문을 입력해 주세요.")
    if any(e.release.family_id == family_id and e.release.version_raw == raw
           and (entry is None or e.release.release_version_id != entry.release.release_version_id)
           for e in catalog.releases):
        raise ValueError("같은 SW 계열에 이미 등록된 버전 원문입니다.")
    order = values.get("version_order")
    if values.get("released_at") is not None and type(values["released_at"]) is not date:
        raise ValueError("릴리즈일은 올바른 날짜 또는 빈 값이어야 합니다.")
    if order is not None and (type(order) is not int or not str(values.get("order_basis", "")).strip()):
        raise ValueError("릴리즈 순서는 정수와 확인 근거를 함께 입력해 주세요.")
    if not any(p.model_id == model_id and p.family_id == family_id for p in catalog.pairs):
        raise ValueError("등록할 모델·SW 계열을 선택해 주세요.")
    rid = entry.release.release_version_id if entry else max((e.release.release_version_id for e in catalog.releases), default=0) + 1
    release = ReleaseVersion(rid, family_id, raw, order, values.get("released_at"))
    return replace(entry, release=release) if entry else ReleaseEntry(
        release, (model_id,), (SourceReference("직접 등록 · 작업본 · 원본 파일 없음"),))


def make_item(catalog, release_id, values, *, entry=None):
    if catalog.entry(release_id) is None:
        raise ValueError("관리할 릴리즈를 먼저 선택해 주세요.")
    content = values.get("content", "")
    if not content.strip():
        raise ValueError("변경 내용을 입력해 주세요.")
    number = values.get("item_no")
    if number is not None and (type(number) is not int or number < 1):
        raise ValueError("Item 번호는 양의 정수 또는 빈 값이어야 합니다.")
    iid = entry.item.item_id if entry else max((e.item.item_id for e in catalog.items), default=0) + 1
    jira_code = values.get("jira_code", values.get("jira_key", values.get("jira"))) or (entry.item.jira_code if entry else None)
    from setron.features.release.validation import validate_values
    validate_values("item", {"content": content, "item_no": number, "jira_code": jira_code})
    item = Item(iid, number, content, jira_code)
    result = replace(entry, item=item, jira_code_known=True) if entry else ItemEntry(
        item, (SourceReference("직접 등록 · 작업본"),), jira_code_known=True)
    scope = TargetScope(values.get("target_scope", "UNKNOWN"))
    ids = tuple(values.get("target_group_ids", ())) if scope is TargetScope.LISTED else ()
    allowed = set(catalog.entry(release_id).model_ids)
    if scope is TargetScope.LISTED and (not ids or not set(ids) <= allowed):
        raise ValueError("이 릴리즈의 확인된 적용 모델을 선택해 주세요.")
    row = VersionItem(release_id, iid, ItemState(values.get("item_state", "UNKNOWN")), scope, ids)
    return result, row


def archive_comparison(first, second):
    """작업별 변경 묶음 비교. 두 전체 현황 스냅샷의 차이로 주장하지 않는다."""
    def flatten(entry):
        return {(change.category, change.target, key): value for change in entry.changes for key, value in change.after}
    old, new = flatten(first), flatten(second)
    keys = sorted(old.keys() | new.keys())
    return tuple((*key, old.get(key, "이 작업에 없음"), new.get(key, "이 작업에 없음"))
                 for key in keys if old.get(key) != new.get(key))
