"""릴리즈 변경과 관련 판정을 하나의 저장 업무로 처리한다 (BE-010).

화면 연결 시 기존 BE-004 저장 서비스 대신 이 진입점을 사용한다.
예: ``ReleaseRevisionService(storage, context).update_release(release_id, changes)``.
반환한 ``result.value``는 관리값, ``result.refresh``는 같은 트랜잭션의 재판정 결과다.
목표 N·모델 적용 범위는 호출자의 명시적 조건이며 영구 저장하거나 자동 변경하지 않는다.
"""

from dataclasses import dataclass

from sqlalchemy import select

from setron.features.equipment.repository import EquipmentRepository
from setron.features.item.schemas import ItemState
from setron.features.storage.inputs import identifier, require

from .refresh import ReleaseRefreshService, _context_copy
from .refresh_schemas import RefreshContext, ReleaseRefresh
from .schemas import ItemInput, ItemLinkInput, ReleaseInput
from .service import ReleaseService
from .validation import prepare_links, prepare_record


@dataclass(frozen=True)
class RevisionResult:
    """저장한 관리값과 그 변경을 반영한 관련 판정 데이터.

    ivar
    - value: 릴리즈 상세·Item 관리값 또는 계열 교체 결과.
    - refresh: 같은 트랜잭션에서 읽고 계산한 최신 판정.
    """

    value: object
    refresh: ReleaseRefresh


class ReleaseRevisionService:
    """릴리즈 변경·교체 및 관련 판정 갱신 서비스.

    저장과 재판정 중 하나라도 실패하면 전체를 취소한다. 최종 commit의 결과가
    불명확한 오류도 그대로 전달하며 요청을 자동으로 재시도하지 않는다.
    """

    def __init__(self, storage, context=None):
        """기존 저장 서비스와 명시적 재판정 조건을 연결한다.

        param
        - storage: 공통 트랜잭션을 제공하는 StorageService.
        - context: RefreshContext. None이면 목표·적용 범위·별칭을 추정하지 않음.

        raises
        - StorageValidationError: context의 자료형이 잘못된 경우.
        """
        require(context is None or isinstance(context, RefreshContext), "재판정 조건이 필요합니다.")
        self.storage = storage
        self.context = context if context is not None else RefreshContext()
        self.releases = ReleaseService(storage)
        self.refresher = ReleaseRefreshService()

    def register_release(self, value: ReleaseInput, items: tuple[ItemInput, ...] = ()) -> RevisionResult:
        """새 릴리즈·Item을 등록하고 미등록 설비 버전과 관련 판정을 갱신한다.

        param
        - value: 검토를 마친 릴리즈 등록값.
        - items: 이 버전에 포함된 새 Item 튜플. 모델 범위는 UNKNOWN으로 시작.

        return
        - 등록한 ReleaseDetail과 전체 과거 현황 재판정.

        raises
        - StorageValidationError: 등록값·원본·재판정 조건이 잘못된 경우.
        - LookupError: 연결할 계열·입력 작업·노트가 없는 경우.
        - DatabaseError: 접속·저장 실패 또는 최종 저장 결과가 불명확한 경우.
        """
        require(type(value) is ReleaseInput, "ReleaseInput 형식이 필요합니다.")
        prepare_record(value)
        context = _context_copy(self.context)
        with self.storage.transaction() as tx:
            self._lock_families(tx, (value.family_id,))
            detail = self.releases.register_release_in(tx, value, items)
            result = self._result(tx, detail, (value.family_id,), context=context)
        return result

    def update_release(self, release_id: int, changes: dict) -> RevisionResult:
        """릴리즈 관리값을 수정하고 비교·목표·Item·위험 판정을 갱신한다.

        param
        - release_id: 수정할 릴리즈 ID.
        - changes: BE-004가 허용하는 관리 필드와 새 값.

        return
        - 원본을 보존한 ReleaseDetail과 재판정. 버전명이 바뀌면 이전 연결을 재확인.

        raises
        - StorageValidationError: 수정값·연결·재판정 조건이 잘못된 경우.
        - LookupError: 수정할 릴리즈가 없는 경우.
        - DatabaseError: 접속·저장 실패 또는 최종 저장 결과가 불명확한 경우.
        """
        identifier(release_id, "release_version_id")
        context = _context_copy(self.context)
        family_id = self._discover_release_family(release_id)
        with self.storage.transaction() as tx:
            before = self._release_family_lock(tx, release_id, family_id)
            release = self.releases._edit(tx, "release_version", {"release_version_id": release_id}, changes)
            invalidated = (release_id,) if before["version_raw"] != release["version_raw"] else ()
            result = self._result(tx, self.releases._detail(tx, release), (release["family_id"],),
                                  invalidated, context=context)
        return result

    def register_item(self, release_id: int, value: ItemInput) -> RevisionResult:
        """기존 릴리즈에 새 Item을 등록하고 적용·위험 판정을 함께 갱신한다.

        param
        - release_id: 새 Item이 포함된 릴리즈 ID.
        - value: 검토한 Item 값. 다른 릴리즈에 자동으로 연결하지 않음.

        return
        - 새 Item의 RecordView와 관련 판정.

        raises
        - StorageValidationError: Item 값·원본·재판정 조건이 잘못된 경우.
        - LookupError: 릴리즈·입력 작업·노트가 없는 경우.
        - DatabaseError: 접속·저장 실패 또는 최종 저장 결과가 불명확한 경우.
        """
        identifier(release_id, "release_version_id")
        require(type(value) is ItemInput, "ItemInput 형식이 필요합니다.")
        values = prepare_record(value)
        context = _context_copy(self.context)
        family_id = self._discover_release_family(release_id)
        with self.storage.transaction() as tx:
            release = self._release_family_lock(tx, release_id, family_id)
            item = self.releases._insert(tx, "item", values, {})
            self.releases._link(tx, release, ItemLinkInput(item_id=item["item_id"], item_state=ItemState.APPLIED))
            view = self.releases.repository.view(tx, "item", item)
            result = self._result(tx, view, (release["family_id"],), context=context)
        return result

    def update_item(self, item_id: int, changes: dict) -> RevisionResult:
        """공유 Item 관리값을 수정하고 연결된 모든 계열의 판정을 갱신한다.

        param
        - item_id: 수정할 Item ID.
        - changes: item_no·content 중 수정할 필드와 값.

        return
        - 원본을 보존한 Item RecordView와 모든 기준일의 판정.

        raises
        - StorageValidationError: 수정값이 잘못됐거나 잠금 중 공유 계열이 바뀐 경우.
        - LookupError: 수정할 Item이 없는 경우.
        - DatabaseError: 접속·저장 실패 또는 최종 저장 결과가 불명확한 경우.
        """
        identifier(item_id, "item_id")
        context = _context_copy(self.context)
        with self.storage.transaction() as discovery:
            discovery.get("item", {"item_id": item_id})
            families = self._item_families(discovery, item_id)
        with self.storage.transaction() as tx:
            self._lock_families(tx, families)
            self.releases.repository.get(tx, "item", {"item_id": item_id}, lock=True)
            require(self._item_families(tx, item_id, lock=True) == families,
                    "Item의 연결 계열이 바뀌었습니다. 최신 자료를 확인한 뒤 다시 수정해 주세요.")
            item = self.releases._edit(tx, "item", {"item_id": item_id}, changes)
            view = self.releases.repository.view(tx, "item", item)
            result = self._result(tx, view, families, context=context)
        return result

    def save_item_links(self, release_id: int, links: tuple[ItemLinkInput, ...]) -> RevisionResult:
        """검토한 Item 포함 상태·모델 범위를 저장하고 관련 판정을 갱신한다.

        param
        - release_id: 연결을 저장할 릴리즈 ID.
        - links: Item 연결 튜플. 요청에 없는 기존 연결은 유지.

        return
        - 변경 후 ReleaseDetail과 전체 과거 현황의 판정.

        raises
        - StorageValidationError: 중복·상태·모델 범위·재판정 조건이 잘못된 경우.
        - LookupError: 릴리즈·Item·적용 모델이 없는 경우.
        - DatabaseError: 접속·저장 실패 또는 최종 저장 결과가 불명확한 경우.
        """
        identifier(release_id, "release_version_id")
        links = prepare_links(links)
        context = _context_copy(self.context)
        family_id = self._discover_release_family(release_id)
        with self.storage.transaction() as tx:
            release = self._release_family_lock(tx, release_id, family_id)
            for link in sorted(links, key=lambda entry: entry.item_id):
                self.releases._link(tx, release, link)
            result = self._result(tx, self.releases._detail(tx, release), (release["family_id"],), context=context)
        return result

    def refresh(self, context=None) -> ReleaseRefresh:
        """현재 저장 자료를 명시한 조회 조건으로 다시 연결·판정한다.

        param
        - context: 이번 호출의 RefreshContext. None이면 생성 시 조건 사용.

        return
        - 모든 실제 기준일의 최신 판정. 목표 N을 DB에 저장하거나 자동 변경하지 않음.

        raises
        - StorageValidationError: 조건·저장 자료의 관계가 잘못된 경우.
        - DatabaseError: 접속·연결 갱신 실패 또는 최종 저장 결과가 불명확한 경우.
        """
        selected = _context_copy(self.context if context is None else context)
        with self.storage.transaction() as discovery:
            families = tuple(row["family_id"] for row in EquipmentRepository().rows(discovery, "version_family"))
        with self.storage.transaction() as tx:
            self._lock_families(tx, families)
            table = tx.repository.table("version_family")
            current = tuple(tx.session.execute(select(table.c.family_id).order_by(table.c.family_id)
                                              .with_for_update()).scalars())
            require(current == families, "계열 목록이 바뀌었습니다. 최신 자료를 확인한 뒤 다시 조회해 주세요.")
            result = self.refresher.refresh_in(tx, context=selected)
        return result

    def preview_replacement(self, request, review=None):
        """쓰기 없이 계열 전체 교체의 매칭·충돌 검토 내용을 조회한다.

        param
        - request: ReplacementRequest. 전체 누적 노트의 검토한 새 자료.
        - review: 선택한 매칭을 반영할 ReplacementReview 또는 None.

        return
        - 최신 DB·요청 지문과 추가 질문·충돌을 포함한 ReplacementPreview.

        raises
        - StorageValidationError: 요청이 잘못됐거나 검토 이후 자료가 바뀐 경우.
        - LookupError: 교체할 계열이 없는 경우.
        """
        from .replacement import FamilyReplacementService

        return FamilyReplacementService(self.storage, _context_copy(self.context)).preview(request, review)

    def replace_family(self, request, review) -> RevisionResult:
        """검토를 완료한 한 계열 전체를 교체하고 관련 판정을 갱신한다.

        param
        - request: 전체 노트·파일명 확인을 마친 ReplacementRequest.
        - review: 최신 검토 지문·매칭·값 선택을 담은 ReplacementReview.

        return
        - 교체한 관리 자료와 같은 저장 묶음에서 재계산한 판정.

        raises
        - ReplacementReviewRequired: 매칭 또는 수정값 선택이 남아 있는 경우.
        - StorageValidationError: 요청이 잘못됐거나 검토 이후 자료가 바뀐 경우.
        - DatabaseError: 교체·재판정 실패 또는 최종 저장 결과가 불명확한 경우.
        """
        from .replacement import FamilyReplacementService

        value, refresh = FamilyReplacementService(self.storage, _context_copy(self.context)).replace(request, review)
        return RevisionResult(value, refresh)

    def _lock_families(self, tx, family_ids):
        """계열 행을 ID 순서로 잠가 같은 계열의 교체·수정을 직렬화한다.

        param
        - tx: 현재 저장 묶음의 공통 트랜잭션.
        - family_ids: 잠글 계열 ID 목록. 빈 목록이면 잠글 계열이 없음.

        raises
        - LookupError: 잠글 계열이 없는 경우.
        - StorageValidationError: 계열 ID 형식이 잘못된 경우.
        """
        for family_id in sorted(set(family_ids)):
            identifier(family_id, "family_id")
            self.releases.repository.get(tx, "version_family", {"family_id": family_id}, lock=True)

    def _discover_release_family(self, release_id):
        """별도 읽기 업무에서 릴리즈의 계열 ID를 확인한다.

        읽기 업무를 끝낸 뒤 쓰기 업무를 시작하므로 MySQL REPEATABLE READ의
        일관 읽기 시점이 계열 잠금 대기 이전으로 고정되지 않는다.

        param
        - release_id: 조회할 릴리즈 ID.

        return
        - 쓰기 업무에서 다시 확인할 계열 ID.

        raises
        - LookupError: 조회할 릴리즈가 없는 경우.
        """
        with self.storage.transaction() as tx:
            return tx.get("release_version", {"release_version_id": release_id})["family_id"]

    def _release_family_lock(self, tx, release_id, family_id):
        """릴리즈가 속한 계열을 먼저 잠그고 현재 릴리즈를 다시 읽는다.

        param
        - tx: 현재 저장 묶음의 공통 트랜잭션.
        - release_id: 조회할 릴리즈 ID.
        - family_id: 별도 읽기 업무에서 확인한 계열 ID.

        return
        - 계열 잠금 후 다시 조회한 릴리즈 행.

        raises
        - LookupError: 조회 중 릴리즈 또는 계열이 사라진 경우.
        - StorageValidationError: 릴리즈의 계열이 사전 확인 결과와 달라진 경우.
        """
        self._lock_families(tx, (family_id,))
        current = self.releases.repository.get(tx, "release_version", {"release_version_id": release_id}, lock=True)
        require(current["family_id"] == family_id,
                "릴리즈의 계열이 바뀌었습니다. 최신 자료를 확인한 뒤 다시 수정해 주세요.")
        return current

    def _item_families(self, tx, item_id, *, lock=False):
        """Item을 명시적으로 연결한 계열 ID를 조회한다.

        param
        - tx: 현재 저장 묶음의 공통 트랜잭션.
        - item_id: 조회할 Item ID.
        - lock: True이면 최신 연결을 잠금 조회해 공유 범위 변경을 확인.

        return
        - 중복을 제외한 계열 ID의 오름차순 튜플. 미연결 Item이면 빈 튜플.
        """
        release = tx.repository.table("release_version")
        link = tx.repository.table("release_version_item")
        query = select(release.c.family_id).join(
            link, release.c.release_version_id == link.c.release_version_id,
        ).where(link.c.item_id == item_id).order_by(release.c.family_id)
        if lock:
            query = query.with_for_update()
        return tuple(sorted(set(tx.session.execute(query).scalars())))

    def _result(self, tx, value, families, invalidated=(), *, context):
        """관리값과 같은 트랜잭션에서 계산한 판정을 묶는다.

        param
        - tx: 아직 확정하지 않은 공통 저장 트랜잭션.
        - value: 반환할 관리값.
        - families: 설비의 릴리즈 연결을 갱신할 계열 ID 튜플.
        - invalidated: 버전명 변경으로 기존 연결을 재확인할 릴리즈 ID 튜플.
        - context: 이번 작업을 시작할 때 복사한 명시적 조회 조건.

        return
        - 바깥쪽 commit 성공 후 호출자에게 전달할 RevisionResult.
        """
        refresh = self.refresher.refresh_in(tx, context=context, family_ids=families,
                                            invalidated_release_ids=invalidated)
        return RevisionResult(value, refresh)
