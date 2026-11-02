"""조회 결과의 확정 필드 변환. DB·Qt·로더를 실행하거나 적용 모델을 추정하지 않는다."""

from dataclasses import dataclass
from datetime import date

from setron.features.aggregation.schemas import PmVersion
from setron.features.equipment.schemas import StoredSnapshot
from setron.features.item.schemas import Item, ItemState, TargetScope, VersionItem
from setron.features.release.schemas import RecordView, ReleaseDetail, SourceCell
from setron.features.release.validation import validate_values
from setron.features.storage.inputs import identifier, require, required_text
from setron.features.version.schemas import ReleaseVersion
from setron.ui.version_data import ItemEntry, SourceReference


@dataclass(frozen=True)
class ReleaseRead:
    """적용 모델을 포함하지 않는 릴리즈 읽기 데이터.

    ivar
    - release: 현재 관리값으로 만든 버전.
    - sources: 원본 셀 위치. 문서명·개정번호는 미확인.
    - items_verified: 저장된 Item 매핑 확인 상태.
    """

    release: ReleaseVersion
    sources: tuple[SourceReference, ...]
    items_verified: bool


def _record(view: RecordView, table: str, fields: tuple[str, ...]) -> dict:
    """필수 조회 필드와 테이블을 검사한다.

    param
    - view: 서비스가 반환한 현재값·출처.
    - table: 기대하는 테이블.
    - fields: None도 명시적으로 전달해야 하는 필드 이름.

    return
    - 변경하지 않은 현재값 딕셔너리.

    raises
    - StorageValidationError: 테이블 또는 필수 필드가 잘못된 경우.
    """
    require(isinstance(view, RecordView) and view.table == table, f"{table}: 조회 결과가 필요합니다.")
    require(isinstance(view.current, dict), "현재값은 딕셔너리여야 합니다.")
    require(all(name in view.current for name in fields), f"{table}: 필수 조회 필드가 누락되었습니다.")
    return view.current


def source_references(view: RecordView) -> tuple[SourceReference, ...]:
    """원본 셀마다 위치를 옮기며 문서명이나 개정번호는 채우지 않는다.

    원본 값과 입력 일시는 이 표시 객체에 포함되지 않는다. 원본 비교에는 RecordView를 사용한다.

    param
    - view: 릴리즈 또는 Item 서비스 조회 결과.

    return
    - 원본 셀 순서의 위치 튜플. 원본 셀이 없으면 빈 튜플.

    raises
    - StorageValidationError: 테이블·입력 작업 ID·셀 위치가 잘못된 경우.
    """
    require(isinstance(view, RecordView) and view.table in {"release_version", "item"},
            "릴리즈 또는 Item 조회 결과가 필요합니다.")
    row = _record(view, view.table, ("batch_id",))
    identifier(row["batch_id"], "batch_id")
    sources = []
    for cell in view.original_cells:
        require(isinstance(cell, SourceCell), "원본 셀 조회 결과가 필요합니다.")
        for name in ("field", "sheet", "address"):
            required_text(getattr(cell, name), name, 65535)
        sources.append(SourceReference(cell.field, sheet=cell.sheet, cells=cell.address,
                                       batch_id=row["batch_id"]))
    return tuple(sources)


def read_release(view: RecordView) -> ReleaseRead:
    """확인된 릴리즈 필드를 읽고 적용 모델은 미정 상태로 남긴다.

    param
    - view: 릴리즈 RecordView. 순서·출시일 미확인은 명시적 None.

    return
    - ReleaseRead. ReleaseEntry/VersionCatalog의 적용 모델은 별도 계약이 필요하다.

    raises
    - StorageValidationError: 필수 필드·ID·버전·순서·출시일·매핑 상태가 잘못된 경우.
    """
    row = _record(view, "release_version", ("release_version_id", "family_id", "version_raw",
                                          "version_order", "released_at", "item_mapping_status"))
    for name in ("release_version_id", "family_id"):
        identifier(row[name], name)
    validate_values("release_version", row)
    release = ReleaseVersion(row["release_version_id"], row["family_id"], row["version_raw"],
                             row["version_order"], row["released_at"])
    return ReleaseRead(release, source_references(view), row["item_mapping_status"] == "VERIFIED")


def read_item(view: RecordView) -> ItemEntry:
    """Item 현재값과 원본 위치를 읽는다.

    param
    - view: Item RecordView. 번호 미확인은 명시적 None.

    return
    - 원본 번호와 시스템 ID를 구분한 ItemEntry.

    raises
    - StorageValidationError: 필수 필드·ID·번호·내용·출처가 잘못된 경우.
    """
    row = _record(view, "item", ("item_id", "item_no", "content"))
    identifier(row["item_id"], "item_id")
    validate_values("item", row)
    return ItemEntry(Item(row["item_id"], row["item_no"], row["content"]), source_references(view))


def read_item_links(detail: ReleaseDetail) -> tuple[VersionItem, ...]:
    """조회된 릴리즈의 명시적 Item 연결만 변환한다.

    param
    - detail: 저장 상태와 대상 범위를 포함한 릴리즈 상세.

    return
    - 저장된 연결 순서의 튜플. 빈 연결로 다른 버전의 Item을 상속하지 않는다.

    raises
    - StorageValidationError: 중복 Item·잘못된 상태·대상 범위가 있는 경우.
    """
    require(isinstance(detail, ReleaseDetail), "릴리즈 상세 조회 결과가 필요합니다.")
    release = read_release(detail.release).release
    result, seen = [], set()
    for link in detail.items:
        item = read_item(link.item).item
        require(item.item_id not in seen, "릴리즈의 Item 연결이 중복되었습니다.")
        seen.add(item.item_id)
        require(isinstance(link.item_state, ItemState), "저장된 Item 상태가 잘못됐습니다.")
        require(isinstance(link.target_scope, TargetScope), "모델 대상 범위가 잘못됐습니다.")
        require(isinstance(link.target_group_ids, tuple), "대상 모델은 튜플이어야 합니다.")
        for model_id in link.target_group_ids:
            identifier(model_id, "target_group_id")
        require(len(set(link.target_group_ids)) == len(link.target_group_ids), "대상 모델이 중복되었습니다.")
        require(bool(link.target_group_ids) == (link.target_scope is TargetScope.LISTED),
                "LISTED에만 비어 있지 않은 대상 모델이 필요합니다.")
        result.append(VersionItem(release.release_version_id, item.item_id, link.item_state,
                                  link.target_scope, link.target_group_ids))
    return tuple(result)


def read_pm_versions(snapshot: StoredSnapshot) -> tuple[PmVersion, ...]:
    """관측된 SW·PM 관계를 유지하며 판정 입력으로 변환한다.

    param
    - snapshot: 특정 관측일의 서비스 결과. PM 없는 SW는 확인된 대표 모델이 필요하다.

    return
    - 모든 SW·PM의 튜플. 날짜·출처·분석 제외 정보는 원본 snapshot에서 별도로 다룬다.

    raises
    - StorageValidationError: 관측일·모델·SW가 잘못됐거나 PM 위치 중복·부분 연결이 있는 경우.
    """
    require(isinstance(snapshot, StoredSnapshot), "저장된 관측 결과가 필요합니다.")
    require(type(snapshot.base_date) is date, "관측 기준일이 필요합니다.")
    if any(sw.pms for sw in snapshot.software):
        require(all(sw.pms for sw in snapshot.software), "모든 SW의 PM 연결을 확인해야 합니다.")
    result, seen = [], set()
    for sw in snapshot.software:
        identifier(sw.family_id, "family_id")
        required_text(sw.version_raw, "version_raw", 100)
        if sw.release_version_id is not None:
            identifier(sw.release_version_id, "release_version_id")
        for pm in sw.pms:
            required_text(pm.pm_name, "pm_name", 50)
        positions = tuple((pm.pm_name, pm.group_id) for pm in sw.pms) if sw.pms else ((None, snapshot.group_id),)
        for name, model_id in positions:
            identifier(model_id, "group_id")
            if name is not None:
                required_text(name, "pm_name", 50)
            key = ("pm", name) if name is not None else ("family", sw.family_id)
            require(key not in seen, "PM 위치 또는 같은 계열의 대표 SW 연결이 중복되었습니다.")
            seen.add(key)
            result.append(PmVersion(name, model_id, sw.family_id, sw.version_raw, sw.release_version_id))
    return tuple(result)
