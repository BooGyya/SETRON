"""릴리즈·Item 등록·수정·조회 업무 처리 (BE-004)."""

from copy import deepcopy

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.storage.inputs import identifier, require

from .repository import ReleaseRepository
from .schemas import ItemInput, ItemLinkInput, LinkedItemView, ReleaseDetail, ReleaseInput
from .validation import prepare_changes, prepare_links, prepare_record


class ReleaseService:
    """릴리즈·Item 관리 서비스.

    검토된 관리값과 명시적인 Item 연결을 저장한다. 설비 적용 판정·위험 재계산은
    수행하지 않는다. DB 오류는 공통 트랜잭션에서 전달하며, 최종 저장 결과가
    불명확한 경우 요청을 자동으로 재시도하지 않는다.
    """

    def __init__(self, storage):
        """공통 저장 서비스를 연결하고 릴리즈 레포지토리를 초기화한다.

        param
        - storage: DB 접속과 업무 단위 트랜잭션을 관리하는 StorageService.
        """
        self.storage = storage
        self.repository = ReleaseRepository()

    def register_release(self, value: ReleaseInput, items: tuple[ItemInput, ...] = ()) -> ReleaseDetail:
        """릴리즈와 그 버전에 포함된 새 Item들을 한 번에 등록한다.

        새 Item은 APPLIED로 연결하고 모델 범위는 UNKNOWN으로 둔다.
        최종 저장 전 작업이 실패하면 이번 요청의 입력 작업·릴리즈·Item·연결을 함께 취소한다.
        최종 저장 중 연결이 끊기면 반영 여부를 확인할 수 없는 오류를 전달한다.

        param
        - value: 검토를 마친 릴리즈 등록 요청.
        - items: 이 릴리즈에 포함된 새 Item 등록 요청 튜플. 없으면 빈 튜플.

        return
        - 원본 비교 정보와 연결 Item을 포함한 ReleaseDetail.

        raises
        - StorageValidationError: 입력 또는 원본·계열 연결이 잘못된 경우.
        - LookupError: 연결할 입력 작업·계열·노트가 없는 경우.
        - DatabaseConnectionError: 등록 작업을 시작하기 전에 DB 접속에 실패한 경우.
        - DatabaseSaveError: 같은 계열의 버전 원문 중복 등으로 DB 저장에 실패한 경우.
        - DatabaseCommitUnknownError: 최종 저장 중 연결이 끊겨 반영 여부를 확인할 수 없는 경우.
        """
        with self.storage.transaction() as tx:
            return self.register_release_in(tx, value, items)

    def register_release_in(self, tx, value: ReleaseInput, items: tuple[ItemInput, ...] = ()) -> ReleaseDetail:
        """호출자가 연 트랜잭션 안에서 릴리즈와 새 Item들을 등록한다.

        입력 작업 생성 등 다른 저장과 한 번에 확정해야 할 때 쓴다. 확정·취소는 호출자가 관리한다.

        param
        - tx: 호출자가 연 StorageTransaction.
        - value: 검토를 마친 릴리즈 등록 요청.
        - items: 이 릴리즈에 포함된 새 Item 등록 요청 튜플. 없으면 빈 튜플.

        return
        - 원본 비교 정보와 연결 Item을 포함한 ReleaseDetail.

        raises
        - StorageValidationError: 입력 또는 원본·계열 연결이 잘못된 경우.
        - LookupError: 연결할 입력 작업·계열·노트가 없는 경우.
        """
        require(type(value) is ReleaseInput, "ReleaseInput 형식이 필요합니다.")
        require(isinstance(items, tuple) and all(type(item) is ItemInput for item in items), "새 Item 요청 튜플이 필요합니다.")
        release_values = prepare_record(value)
        item_values = tuple(prepare_record(item) for item in items)
        manual_batch = {}
        release = self._insert(tx, "release_version", release_values, manual_batch)
        for values in item_values:
            item = self._insert(tx, "item", values, manual_batch)
            self._link(tx, release, ItemLinkInput(item_id=item["item_id"], item_state=ItemState.APPLIED))
        return self._detail(tx, release)

    def register_item(self, release_id: int, value: ItemInput):
        """기존 릴리즈에 포함된 새 Item을 등록한다.

        Item 생성과 APPLIED 연결을 같은 트랜잭션에 저장한다. 모델 범위는
        UNKNOWN으로 두며 다른 릴리즈에는 연결을 추가하지 않는다.

        param
        - release_id: 새 Item을 연결할 릴리즈 ID.
        - value: 검토를 마친 Item 등록 요청.

        return
        - 등록한 Item의 원본·현재 관리값 비교 결과.

        raises
        - StorageValidationError: 입력이나 원본 위치가 잘못된 경우.
        - LookupError: 릴리즈·입력 작업·노트가 없는 경우.
        - DatabaseConnectionError: 등록 작업을 시작하기 전에 DB 접속에 실패한 경우.
        - DatabaseSaveError: Item 또는 연결을 DB에 저장하지 못한 경우.
        - DatabaseCommitUnknownError: 최종 저장 중 연결이 끊겨 반영 여부를 확인할 수 없는 경우.
        """
        identifier(release_id, "release_version_id")
        require(type(value) is ItemInput, "ItemInput 형식이 필요합니다.")
        values = prepare_record(value)
        with self.storage.transaction() as tx:
            release = self.repository.get(tx, "release_version", {"release_version_id": release_id}, lock=True)
            item = self._insert(tx, "item", values, {})
            self._link(tx, release, ItemLinkInput(item_id=item["item_id"], item_state=ItemState.APPLIED))
            return self.repository.view(tx, "item", item)

    def update_release(self, release_id: int, changes: dict) -> ReleaseDetail:
        """릴리즈의 편집 가능한 관리값을 수정하고 원본 정보를 유지한다.

        param
        - release_id: 수정할 릴리즈 ID.
        - changes: version_raw, version_suffix, released_at, version_order, item_mapping_status 중 변경할 값.

        return
        - 수정 후 다시 조회한 ReleaseDetail.

        raises
        - StorageValidationError: 편집할 수 없는 필드가 있거나 변경값이 잘못된 경우.
        - LookupError: 수정 대상 릴리즈가 없는 경우.
        - DatabaseConnectionError: 수정 작업을 시작하기 전에 DB 접속에 실패한 경우.
        - DatabaseSaveError: 버전 원문 중복 등으로 DB 수정에 실패한 경우.
        - DatabaseCommitUnknownError: 최종 저장 중 연결이 끊겨 반영 여부를 확인할 수 없는 경우.
        """
        identifier(release_id, "release_version_id")
        with self.storage.transaction() as tx:
            release = self._edit(tx, "release_version", {"release_version_id": release_id}, changes)
            return self._detail(tx, release)

    def update_item(self, item_id: int, changes: dict):
        """Item 번호와 업데이트 내용을 수정한다.

        같은 Item을 여러 릴리즈가 공유하면 수정한 관리값도 함께 조회된다.
        원본 입력 작업과 셀 위치·값, 릴리즈별 포함 상태·모델 범위는 변경하지 않는다.

        param
        - item_id: 수정할 Item ID.
        - changes: item_no와 content 중 변경할 항목과 값.

        return
        - 수정 후 Item의 원본·현재 관리값 비교 결과.

        raises
        - StorageValidationError: 편집할 수 없는 필드가 있거나 변경값이 잘못된 경우.
        - LookupError: 수정 대상 Item이 없는 경우.
        - DatabaseConnectionError: 수정 작업을 시작하기 전에 DB 접속에 실패한 경우.
        - DatabaseSaveError: Item 관리값을 DB에서 수정하지 못한 경우.
        - DatabaseCommitUnknownError: 최종 저장 중 연결이 끊겨 반영 여부를 확인할 수 없는 경우.
        """
        identifier(item_id, "item_id")
        with self.storage.transaction() as tx:
            item = self._edit(tx, "item", {"item_id": item_id}, changes)
            return self.repository.view(tx, "item", item)

    def save_item_links(self, release_id: int, links: tuple[ItemLinkInput, ...]) -> ReleaseDetail:
        """기존 Item의 릴리즈 연결·포함 상태·모델 범위를 등록하거나 수정한다.

        요청에 없는 연결은 그대로 유지하며 다른 릴리즈로 상태를 자동 전파하지 않는다.

        param
        - release_id: Item 연결을 저장할 릴리즈 ID.
        - links: 검토한 Item 포함 상태와 적용 모델 범위 튜플. 빈 튜플이면 기존 연결을 유지.

        return
        - 모든 기존 연결을 포함한 저장 후 ReleaseDetail.

        raises
        - StorageValidationError: Item·모델 범위가 중복되거나 설비군이 맞지 않는 경우.
        - LookupError: 릴리즈·Item·모델이 없는 경우.
        - DatabaseConnectionError: 저장 작업을 시작하기 전에 DB 접속에 실패한 경우.
        - DatabaseSaveError: Item 연결을 DB에 저장하지 못한 경우.
        - DatabaseCommitUnknownError: 최종 저장 중 연결이 끊겨 반영 여부를 확인할 수 없는 경우.
        """
        identifier(release_id, "release_version_id")
        links = prepare_links(links)
        with self.storage.transaction() as tx:
            release = self.repository.get(tx, "release_version", {"release_version_id": release_id}, lock=True)
            for link in sorted(links, key=lambda value: value.item_id):
                self._link(tx, release, link)
            return self._detail(tx, release)

    def get_release(self, release_id: int) -> ReleaseDetail:
        """릴리즈의 관리값·원본 셀·연결 Item을 조회한다.

        param
        - release_id: 조회할 릴리즈 ID.

        return
        - 저장된 Item 포함 상태와 모델 범위를 포함한 ReleaseDetail.

        raises
        - LookupError: 해당 릴리즈가 없는 경우.
        - StorageValidationError: ID나 보관된 출처가 잘못된 경우.
        """
        identifier(release_id, "release_version_id")
        with self.storage.transaction() as tx:
            return self._detail(tx, tx.get("release_version", {"release_version_id": release_id}))

    def get_item(self, item_id: int):
        """Item의 현재 관리값과 보관된 원본 셀을 조회한다.

        param
        - item_id: 조회할 Item ID.

        return
        - Item의 원본·현재 관리값 비교 결과.

        raises
        - LookupError: 해당 Item이 없는 경우.
        - StorageValidationError: ID나 보관된 출처가 잘못된 경우.
        """
        identifier(item_id, "item_id")
        with self.storage.transaction() as tx:
            return self.repository.view(tx, "item", tx.get("item", {"item_id": item_id}))

    def list_groups(self):
        """저장된 릴리즈의 계열 관리 모델 목록을 조회한다.

        return
        - 원본 조회 선택지로 사용할 관리 모델 튜플. 릴리즈가 없으면 빈 튜플이며 실제 적용 모델 전체 목록을 뜻하지 않음.
        """
        with self.storage.transaction() as tx:
            return self.repository.groups(tx)

    def list_families(self, group_id: int):
        """관리 모델 아래에 릴리즈가 저장된 버전 계열 목록을 조회한다.

        param
        - group_id: 조회할 계열 관리 모델 ID.

        return
        - 릴리즈가 저장된 계열 목록. 자료가 없으면 빈 튜플.

        raises
        - LookupError: 해당 관리 모델이 없는 경우.
        - StorageValidationError: 관리 모델 ID가 양의 정수 범위를 벗어나거나 정수가 아닌 경우.
        """
        identifier(group_id, "group_id")
        with self.storage.transaction() as tx:
            tx.get("equipment_group", {"group_id": group_id})
            return self.repository.families(tx, group_id)

    def list_releases(self, family_id: int):
        """계열별 릴리즈 목록을 원본 비교 정보와 함께 조회한다.

        param
        - family_id: 조회할 버전 계열 ID.

        return
        - 확인된 순서의 오름차순 RecordView 튜플. 순서 미확인은 마지막이며 자료가 없으면 빈 튜플.

        raises
        - LookupError: 해당 계열이 없는 경우.
        - StorageValidationError: 계열 ID가 양의 정수 범위를 벗어나거나 정수가 아닌 경우.
        """
        identifier(family_id, "family_id")
        with self.storage.transaction() as tx:
            tx.get("version_family", {"family_id": family_id})
            return tuple(self.repository.view(tx, "release_version", row) for row in self.repository.releases(tx, family_id))

    def _insert(self, tx, table, values, manual_batch):
        """원본 및 연결 정보를 확인하고 새 릴리즈 또는 Item을 저장한다.

        원본 셀은 등록 후 비교 결과를 구성할 때 존재 여부를 확인한다.
        릴리즈 등록일 때는 노트와 버전 계열의 설비군도 비교한다.

        param
        - tx: 현재 등록 요청이 공유하는 저장 트랜잭션.
        - table: release_version 또는 item.
        - values: 형식을 검증한 등록 관리값.
        - manual_batch: 이번 요청에서 생성한 MANUAL 입력 작업 ID를 재사용할 객체.

        return
        - 생성된 관리 행.

        raises
        - StorageValidationError: 원본 위치가 잘못됐거나 릴리즈 등록 시 노트와 계열의 설비군이 다른 경우.
        - LookupError: 연결할 입력 작업·노트·계열이 없는 경우.
        """
        values = deepcopy(values)
        if values["batch_id"] is None:
            if not manual_batch:
                manual_batch.update(tx.insert("import_batch", {"source_type": "MANUAL"}))
            values["batch_id"] = manual_batch["batch_id"]
        batch = tx.get("import_batch", {"batch_id": values["batch_id"]})
        if table == "item" and values.get("jira_key") is None:
            from setron.features.item.jira import extract_jira_from_source
            from setron.features.storage.inputs import merge_sources
            from setron.features.storage.validation import StorageValidationError
            try:
                jira = extract_jira_from_source(batch.get("raw_data"), values.get("source_reference"), content=values["content"])
            except ValueError as error:
                raise StorageValidationError(str(error)) from error
            values["jira_key"] = jira.jira_key
            values["source_reference"] = merge_sources(values.get("source_reference"), jira.source_reference)
        require(batch["source_type"] != "EXCEL" or bool(values["source_reference"]), "Excel 자료에는 원본 셀 위치가 필요합니다.")
        note = tx.get("release_note", {"release_note_id": values["release_note_id"]}) if values["release_note_id"] is not None else None
        if table == "release_version":
            family = tx.get("version_family", {"family_id": values["family_id"]})
            if note:
                owner = tx.get("equipment_group", {"group_id": family["group_id"]})
                document_group = tx.get("equipment_group", {"group_id": note["group_id"]})
                require(owner["category"] == document_group["category"], "릴리즈 노트와 버전 계열의 설비군이 다릅니다.")
        record = tx.insert(table, values)
        self.repository.view(tx, table, record)
        return record

    def _edit(self, tx, table, key, changes):
        """현재 행을 잠근 뒤 관리값과 실제 수정 항목만 갱신한다.

        param
        - tx: 현재 수정 요청의 공통 저장 트랜잭션.
        - table: release_version 또는 item.
        - key: 수정 대상의 전체 기본키.
        - changes: 변경할 관리 항목과 값.

        return
        - 수정 후 다시 조회한 관리 행.

        raises
        - StorageValidationError: 원본·식별자를 수정하거나 입력값이 잘못된 경우.
        - LookupError: 수정할 기록이 없는 경우.
        """
        current = self.repository.get(tx, table, key, lock=True)
        return tx.update(table, key, prepare_changes(table, current, changes))

    def _link(self, tx, release, link):
        """한 Item의 릴리즈 연결과 모델 범위를 검증한 뒤 저장한다.

        지정 모델의 존재와 설비군을 검사한다. 실제 모델 적용 범위와 Item 포함 여부는
        호출자가 검토한 값을 사용하며 릴리즈 순서나 모델명으로 추정하지 않는다.

        param
        - tx: 현재 요청의 공통 저장 트랜잭션.
        - release: 잠금 또는 신규 생성한 릴리즈 행.
        - link: 형식을 검증한 ItemLinkInput.

        raises
        - LookupError: Item·계열·계열 관리 모델 또는 지정 모델이 없는 경우.
        - StorageValidationError: 적용 모델과 릴리즈 계열의 설비군이 다른 경우.
        """
        self.repository.get(tx, "item", {"item_id": link.item_id}, lock=True)
        if link.target_group_ids:
            family = tx.get("version_family", {"family_id": release["family_id"]})
            owner = tx.get("equipment_group", {"group_id": family["group_id"]})
            for model_id in sorted(link.target_group_ids):
                model = self.repository.get(tx, "equipment_group", {"group_id": model_id}, lock=True)
                require(model["category"] == owner["category"], "적용 모델과 릴리즈 계열의 설비군이 다릅니다.")
        key = {"release_version_id": release["release_version_id"], "item_id": link.item_id}
        values = {"item_state": link.item_state.value, "target_scope": link.target_scope.value,
                  "target_group_ids": list(link.target_group_ids) if link.target_scope is TargetScope.LISTED else None}
        try:
            tx.get("release_version_item", key)
        except LookupError:
            tx.insert("release_version_item", {**key, **values})
        else:
            tx.update("release_version_item", key, values)

    def _detail(self, tx, release):
        """릴리즈와 명시적으로 연결된 모든 Item의 상세 결과를 구성한다.

        param
        - tx: 현재 조회에 사용하는 공통 저장 트랜잭션.
        - release: 조회 대상 릴리즈 행.

        return
        - 원본 비교 정보와 Item 연결을 포함한 ReleaseDetail.
        """
        items = tuple(LinkedItemView(
            item=self.repository.view(tx, "item", tx.get("item", {"item_id": link["item_id"]})),
            item_state=ItemState(link["item_state"]), target_scope=TargetScope(link["target_scope"]),
            target_group_ids=tuple(link["target_group_ids"] or ()),
        ) for link in self.repository.links(tx, release["release_version_id"]))
        return ReleaseDetail(release=self.repository.view(tx, "release_version", release), items=items)
