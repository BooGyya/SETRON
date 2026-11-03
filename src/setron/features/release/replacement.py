"""검토한 누적 릴리즈 노트의 계열별 전체 교체 (BE-010).

검토는 읽기 전용이며 매칭·값 선택과 DB 상태를 저장 직전에 다시 확인한다.
같은 항목의 ID는 유지하되 원본 출처는 새 입력 작업으로 교체한다.
"""

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import select

from setron.features.equipment.repository import EquipmentRepository
from setron.features.storage.inputs import identifier, merge_sources, require, required_text
from setron.features.storage.validation import decode_record, encode_record

from .refresh import ReleaseRefreshService
from .refresh_schemas import RefreshContext
from .replacement_schemas import (
    MatchQuestion, ReplacementPreview, ReplacementRequest, ReplacementReview,
    ReplacementReviewRequired, ValueConflict, ReplacementItem, ReplacementRelease, ReplacementLink, RiskReconnection,
    LinkConflict,
)
from .schemas import ItemInput, ItemLinkInput, ReleaseInput
from .service import ReleaseService
from .validation import ITEM_EDITABLE, RELEASE_EDITABLE, prepare_links, prepare_record


class FamilyReplacementService:
    """계열 전체 교체 서비스."""

    def __init__(self, storage, context=None):
        """저장소와 요청 시점의 목표·확정 대응을 연결한다.

        param
        - storage: 공통 StorageService.
        - context: 호출자가 확인한 RefreshContext. None이면 목표·적용 범위 미확인.
        """
        self.storage = storage
        self.context = deepcopy(context) if context is not None else RefreshContext()
        self.records = EquipmentRepository()
        self.releases = ReleaseService(storage)

    def preview(self, request, review=None):
        """현재값과 새 원본의 매칭 후보·수정값 충돌을 쓰기 없이 조회한다.

        param
        - request: 계열 전체의 ReplacementRequest.
        - review: 먼저 선택한 매칭. None이면 자동으로 확실한 항목만 연결.

        return
        - 현재 DB·요청 지문과 미완료 질문·충돌을 담은 ReplacementPreview.

        raises
        - StorageValidationError: 입력 형식·선택·원본이 잘못됐거나 오래된 검토인 경우.
        """
        with self.storage.transaction() as tx:
            return self.preview_in(tx, request, review)

    def preview_in(self, tx, request, review=None):
        """호출자가 연 트랜잭션 안에서 교체 검토 내용을 쓰기 없이 구성한다.

        param
        - tx: 여러 시트·계열이 함께 사용하는 공통 트랜잭션.
        - request: 계열 전체의 ReplacementRequest.
        - review: 앞서 선택한 동일 항목·충돌값. None이면 초기 검토.

        return
        - 현재 DB·요청 지문과 미완료 질문·충돌을 담은 ReplacementPreview.

        raises
        - StorageValidationError: 입력·선택 형식이 잘못됐거나 오래된 검토인 경우.
        """
        request, review = deepcopy((request, review))
        self._validate(request)
        state = self._state(tx, request.family_id)
        return self._preview(tx, request, state, review)

    def replace(self, request, review):
        """검토한 계열 자료와 관련 판정을 한 트랜잭션으로 교체한다.

        param
        - request: 파일명과 전체 노트 검토가 확인된 ReplacementRequest.
        - review: 같은 DB·요청 지문의 매칭·값 선택.

        return
        - 저장 후 릴리즈 상세 튜플과 ReleaseRefresh. commit 성공 후에만 반환.

        raises
        - ReplacementReviewRequired: 불확실한 매칭이나 값 충돌 선택이 남은 경우.
        - StorageValidationError: 오래된 검토·잘못된 선택·원본·모델 범위인 경우.
        - DatabaseSaveError: 교체 또는 판정 갱신 실패. 이번 묶음 전체를 취소.
        - DatabaseCommitUnknownError: 최종 저장 여부가 불명확한 경우. 자동 재시도하지 않음.
        """
        with self.storage.transaction() as tx:
            return self.replace_in(tx, request, review)

    def replace_in(self, tx, request, review, *, refresh=True):
        """호출자가 연 저장 업무에서 교체한다. 확정·취소는 호출자가 관리한다.

        param
        - tx: 여러 시트·계열의 변경을 모두 저장하거나 취소할 공통 트랜잭션.
        - request: 전체 노트·파일명 검토를 마친 ReplacementRequest.
        - review: 같은 DB·요청 지문에 대한 명시적인 사용자 선택.
        - refresh: True이면 같은 업무에서 재판정. False이면 호출자가 전체 묶음 저장 후 재판정.

        return
        - 저장 후 릴리즈 상세 튜플과 ReleaseRefresh. refresh=False이면 두 번째 값은 None.

        raises
        - ReplacementReviewRequired: 항목·값·Item 연결의 충돌 선택이 남은 경우.
        - StorageValidationError: 검토·파일명 미확인, 오래된 지문 또는 잘못된 입력인 경우.
        """
        request, review = deepcopy((request, review))
        self._validate(request)
        require(type(refresh) is bool, "재판정 여부는 불리언이어야 합니다.")
        require(type(review) is ReplacementReview, "저장 전에 교체 검토 결과가 필요합니다.")
        require(request.complete_note_confirmed is True, "전체 누적 노트·포함·제외·분기를 먼저 확인해 주세요.")
        require(request.filename_confirmed is True, "저장 전 원본 파일명을 확인해 주세요.")
        state = self._state(tx, request.family_id, lock=True)
        preview = self._preview(tx, request, state, review)
        if preview.questions or preview.conflicts or preview.link_conflicts:
            raise ReplacementReviewRequired(preview)
        values = self._apply(tx, request, state, preview, review)
        refreshed = (ReleaseRefreshService().refresh_in(tx, context=self.context, family_ids=(request.family_id,),
                     invalidated_applicability_release_ids=tuple(value.release.current["release_version_id"] for value in values))
                     if refresh else None)
        return values, refreshed

    def _validate(self, request):
        """새 원본 매칭에 필요한 식별자·출처와 전체 입력 형식을 검사한다.

        param
        - request: 검사할 계열 전체 교체 요청.

        raises
        - StorageValidationError: 형식·중복·출처·참조가 잘못된 경우.
        """
        require(type(request) is ReplacementRequest, "ReplacementRequest 형식이 필요합니다.")
        identifier(request.family_id, "family_id")
        require(type(request.releases) is tuple and type(request.items) is tuple, "전체 릴리즈·Item 튜플이 필요합니다.")
        require(type(request.complete_note_confirmed) is bool and type(request.filename_confirmed) is bool,
                "검토·파일명 확인 여부는 불리언이어야 합니다.")
        encode_record("import_batch", {"source_type": "EXCEL", "raw_data": request.raw_data}, inserting=True)
        require(isinstance(request.raw_data, dict), "새 원본 셀 객체가 필요합니다.")
        item_keys, release_keys, linked = set(), set(), set()
        require(all(type(entry) is ReplacementItem for entry in request.items)
                and all(type(entry) is ReplacementRelease for entry in request.releases), "교체 항목의 형식이 잘못됐습니다.")
        for entry in (*request.items, *request.releases):
            required_text(entry.key, "key", 100)
            keys = release_keys if type(entry) is ReplacementRelease else item_keys
            require(type(entry.value) is (ReleaseInput if type(entry) is ReplacementRelease else ItemInput),
                    "교체 항목과 등록값의 형식이 맞지 않습니다.")
            require(entry.key not in keys, "요청 안의 항목 키가 중복되었습니다.")
            keys.add(entry.key)
            values = prepare_record(entry.value)
            require(values["batch_id"] is None, "새 입력 작업은 교체 서비스에서 생성합니다.")
            require(bool(values["source_reference"]), "새 Excel 자료의 원본 셀 위치가 필요합니다.")
            self._source(request.raw_data, values, None)
        for entry in request.releases:
            require(entry.value.family_id == request.family_id, "다른 계열의 릴리즈를 함께 교체할 수 없습니다.")
            require(type(entry.links) is tuple, "버전별 Item 연결 튜플이 필요합니다.")
            require(all(type(link) is ReplacementLink for link in entry.links), "교체 Item 연결 형식이 잘못됐습니다.")
            for link in entry.links:
                required_text(link.item_key, "item_key", 100)
            require(len({link.item_key for link in entry.links}) == len(entry.links), "버전별 Item 연결이 중복되었습니다.")
            for link in entry.links:
                require(link.item_key in item_keys, "요청에 없는 Item 연결입니다.")
                prepare_links((ItemLinkInput(item_id=1, item_state=link.item_state,
                    target_scope=link.target_scope, target_group_ids=link.target_group_ids),))
                linked.add(link.item_key)
        require(linked == item_keys, "모든 새 Item의 릴리즈 연결이 필요합니다.")

    def _source(self, raw, row, field):
        """필드의 원본 셀을 검증하고 하나의 확인된 원본값만 반환한다.

        param
        - raw: 입력 작업에 보관한 시트·셀 값.
        - row: 관리값과 source_reference·edited_fields.
        - field: 원본값을 읽을 필드. None이면 전체 출처만 검증.

        return
        - 동일한 원본값 하나. 여러 값·편집 전 값 미확인이면 None.

        raises
        - StorageValidationError: 원본에 없는 시트·셀을 참조한 경우.
        """
        values = []
        for name, locations in (merge_sources(row.get("source_reference")) or {}).items():
            for location in locations if isinstance(locations, list) else (locations,):
                sheet = raw.get(location["sheet"]) if isinstance(raw, dict) else None
                require(isinstance(sheet, dict) and all(cell in sheet for cell in location["cells"]),
                        "새 입력 작업에 없는 시트·셀을 참조합니다.")
                if name == field:
                    values.extend(sheet[cell] for cell in location["cells"])
        if field is None:
            return None
        if values:
            return values[0] if all(type(v) is type(values[0]) and v == values[0] for v in values) else None
        return row.get(field) if field not in row.get("edited_fields", ()) else None

    def _state(self, tx, family_id, lock=False):
        """교체 대상과 공유 관계·수동 위험·원본을 같은 세션에서 확보한다.

        param
        - tx: 공통 트랜잭션.
        - family_id: 교체할 계열.
        - lock: 저장 시 계열과 관련 행을 잠글지 여부.

        return
        - 지문과 교체에 사용할 현재 DB 자료.
        """
        self.releases.repository.get(tx, "version_family", {"family_id": family_id}, lock=lock)
        releases = self._rows(tx, "release_version", lock, family_id=family_id)
        ids = {row["release_version_id"] for row in releases}
        links = self.records.rows(tx, "release_version_item")
        item_ids = {row["item_id"] for row in links if row["release_version_id"] in ids}
        risks = self._rows(tx, "risk_rule", False, family_id=family_id)
        risk_item_ids = {row["item_id"] for row in risks if row["item_id"] is not None} - item_ids
        # 수동 위험만 참조하는 Item도 원본을 확보하되 교체 매칭·삭제 대상과 분리한다.
        loaded_items = {i: self.releases.repository.get(tx, "item", {"item_id": i}, lock=lock)
                        for i in sorted(item_ids | risk_item_ids)}
        items = [loaded_items[i] for i in sorted(item_ids)]
        if lock:
            # 공통 삭제 경로와 같은 Item→위험 지정 순서로 잠그고 대기 중 대상 변경을 재확인한다.
            risks = self._rows(tx, "risk_rule", True, family_id=family_id)
            current_targets = {row["item_id"] for row in risks if row["item_id"] is not None}
            require(current_targets <= set(loaded_items), "위험 지정의 대상이 바뀌었습니다. 다시 조회·검토해 주세요.")
            risk_item_ids = current_targets - item_ids
            # 다른 계열의 연결도 Item 잠금을 획득한 뒤 최신 값으로 다시 확인한다.
            links = self._rows(tx, "release_version_item", True)
        risk_items = [loaded_items[i] for i in sorted(risk_item_ids)]
        batches = {row["batch_id"] for row in (*releases, *items, *risk_items)}
        return {"releases": releases, "items": items, "risk_items": risk_items, "risks": risks,
                "links": [row for row in links if row["release_version_id"] in ids or row["item_id"] in item_ids],
                "batches": [tx.get("import_batch", {"batch_id": i}) for i in sorted(batches)]}

    def _rows(self, tx, table_name, lock, **filters):
        """조건에 맞는 행을 잠금 선택과 함께 조회한다.

        param
        - tx: 공통 트랜잭션.
        - table_name: 조회 테이블 이름.
        - lock: 행 잠금 여부.
        - filters: 필터 필드·값.

        return
        - 기본키 순서의 복원한 행 목록.
        """
        table = tx.repository.table(table_name)
        query = select(table).filter_by(**filters).order_by(*table.primary_key.columns)
        if lock:
            query = query.with_for_update()
        return [decode_record(table_name, dict(row)) for row in tx.session.execute(query).mappings()]

    def _preview(self, tx, request, state, review):
        """같은 원본 식별값과 명시 선택으로 매칭·충돌 검토를 구성한다.

        param
        - tx: 읽기 또는 저장 트랜잭션.
        - request: 전체 계열 요청.
        - state: 잠금 여부가 결정된 현재 자료.
        - review: 사용자가 고른 매칭·값. None이면 초기 검토.

        return
        - 현재 상태의 ReplacementPreview.

        raises
        - StorageValidationError: 오래된 지문·잘못된 선택·중복 매칭인 경우.
        """
        fingerprint = sha256(json.dumps([asdict(request), state], ensure_ascii=False, sort_keys=True,
            default=self._json_value, allow_nan=False).encode("utf-8")).hexdigest()
        if review is not None:
            require(type(review) is ReplacementReview and review.fingerprint == fingerprint,
                    "검토 후 자료가 바뀌었습니다. 다시 조회·검토해 주세요.")
            require(type(review.release_matches) is dict and type(review.item_matches) is dict
                    and type(review.choices) is dict and type(review.link_choices) is dict,
                    "매칭·값·Item 연결 선택 객체가 필요합니다.")
            require(type(review.risk_confirmations) is tuple, "위험 확인 ID 튜플이 필요합니다.")
            for rule_id in review.risk_confirmations:
                identifier(rule_id, "risk_rule_id")
            require(len(set(review.risk_confirmations)) == len(review.risk_confirmations)
                    and set(review.risk_confirmations) <= {r["risk_rule_id"] for r in state["risks"]},
                    "같은 계열의 기존 위험만 확인할 수 있습니다.")
            require(all(type(key) is tuple and len(key) == 3 for key in review.choices), "충돌 선택 키 형식이 잘못됐습니다.")
            require(all(all(type(part) is str for part in key) and type(choice) is str
                        and choice in {"CURRENT", "INCOMING"} for key, choice in review.choices.items()),
                    "충돌 선택 키·값 형식이 잘못됐습니다.")
            require(all(type(key) is tuple and len(key) == 2 and all(type(part) is str for part in key)
                        and type(choice) is str and choice in {"CURRENT", "INCOMING"}
                        for key, choice in review.link_choices.items()), "Item 연결 선택 키·값 형식이 잘못됐습니다.")
        batches = {r["batch_id"]: r["raw_data"] for r in state["batches"]}
        old_releases = {r["release_version_id"]: r for r in state["releases"]}
        old_items = {r["item_id"]: r for r in state["items"]}
        questions, conflicts = [], []
        release_matches, item_matches = {}, {}

        def match(kind, key, candidates, fallback):
            """확실한 하나 또는 사용자가 선택한 동일 항목만 연결한다.

            param
            - kind: release 또는 item.
            - key: 새 항목 키.
            - candidates: 같은 원본 식별값을 가진 후보.
            - fallback: 원본 식별이 불확실한 경우의 검토 범위.

            return
            - 확정한 기존 ID 또는 신규 None. 미완료 질문도 별도로 추가.
            """
            selected = getattr(review, f"{kind}_matches", {}) if review else {}
            allowed = set(fallback) | set(candidates)
            if key in selected:
                value = selected[key]
                require(value is None or type(value) is int and value in allowed, "같은 계열의 검토 후보만 선택할 수 있습니다.")
                return value
            if len(candidates) == 1:
                return next(iter(candidates))
            if allowed:
                questions.append(MatchQuestion(kind, key, tuple(sorted(allowed)),
                    "원본 식별값 누락·번호 재배치·중복 후보를 확인해 주세요."))
            return None

        for entry in request.releases:
            new_raw = self._source(request.raw_data, asdict(entry.value), "version_raw")
            candidates = {i for i, row in old_releases.items()
                if new_raw is not None and self._source(batches[row["batch_id"]], row, "version_raw") == new_raw}
            # 일치하는 원문이 없는 새 릴리즈는 신규로 둘 수 있다. 기존 편집 원문이 미확인이면 검토한다.
            uncertain = {i for i, row in old_releases.items()
                if self._source(batches[row["batch_id"]], row, "version_raw") is None}
            release_matches[entry.key] = match("release", entry.key, candidates,
                set(old_releases) if new_raw is None else uncertain)
        membership = defaultdict(set)
        for entry in request.releases:
            old_id = release_matches[entry.key]
            for link in entry.links:
                membership[link.item_key].update(r["item_id"] for r in state["links"] if r["release_version_id"] == old_id)
        for entry in request.items:
            number = self._number(self._source(request.raw_data, asdict(entry.value), "item_no"))
            relevant = membership[entry.key]
            candidates = {i for i in relevant if number is not None
                and self._number(self._source(batches[old_items[i]["batch_id"]], old_items[i], "item_no")) == number}
            item_matches[entry.key] = match("item", entry.key, candidates, relevant)
        for kind, entries, matches, rows, fields in (
            ("release", request.releases, release_matches, old_releases, RELEASE_EDITABLE),
            ("item", request.items, item_matches, old_items, ITEM_EDITABLE),
        ):
            matched = [i for i in matches.values() if i is not None]
            require(len(matched) == len(set(matched)), "같은 기존 항목을 여러 새 항목에 중복 연결할 수 없습니다.")
            selected = getattr(review, f"{kind}_matches", {}) if review else {}
            require(set(selected) <= set(matches), "요청에 없는 매칭 선택입니다.")
            for entry in entries:
                if matches[entry.key] is None:
                    continue
                current = rows[matches[entry.key]]
                for name in sorted(fields):
                    incoming = getattr(entry.value, name)
                    if current[name] == incoming:
                        continue
                    if kind == "release" and name not in current["edited_fields"]:
                        continue
                    choice = review.choices.get((kind, entry.key, name)) if review else None
                    if choice is None:
                        conflicts.append(ValueConflict(kind, entry.key, name, deepcopy(current[name]), incoming))
                    else:
                        require(choice in {"CURRENT", "INCOMING"}, "충돌값은 CURRENT 또는 INCOMING으로 선택해 주세요.")
        if review:
            for (kind, key, name), choice in review.choices.items():
                require(kind in {"release", "item"} and key in (release_matches if kind == "release" else item_matches),
                        "요청에 없는 충돌 선택입니다.")
                require(name in (RELEASE_EDITABLE if kind == "release" else ITEM_EDITABLE)
                        and choice in {"CURRENT", "INCOMING"}, "잘못된 충돌 선택입니다.")
        link_conflicts, _ = self._review_links(request, state, release_matches, item_matches, review)
        risk_reconnections = self._risk_reconnections(request, state, release_matches, item_matches, review)
        if review:
            restorable = {proposal.risk_rule_id for proposal in risk_reconnections if proposal.can_restore}
            require(set(review.risk_confirmations) <= restorable,
                    "대상·해제 경계·순서를 확인할 수 없는 위험은 재연결을 확정할 수 없습니다.")
        return ReplacementPreview(fingerprint, release_matches, item_matches, tuple(questions), tuple(conflicts),
                                  risk_reconnections, link_conflicts)

    @staticmethod
    def _review_links(request, state, release_matches, item_matches, review):
        """기존 포함 상태·모델 범위를 새 값과 비교하고 명시 선택을 반영한다.

        저장된 연결에는 수동 편집 여부의 이력이 없으므로 서로 다른 값은 모두
        선택 대상으로 제공한다. 상태·범위·모델 ID를 원자적인 한 묶음으로 선택한다.

        param
        - request, state: 새 전체 노트와 검토한 현재 연결 행.
        - release_matches, item_matches: 새 요청 키별 확인된 기존 ID 또는 신규 None.
        - review: 기존값 또는 새 값 선택. None이면 선택되지 않은 차이를 모두 반환.

        return
        - 미완료 LinkConflict 튜플과 (릴리즈 키, Item 키)별 선택한 ReplacementLink.

        raises
        - StorageValidationError: 기존 연결이 없는 항목의 값을 선택한 경우.
        """
        stored = {(row["release_version_id"], row["item_id"]): row for row in state["links"]}
        choices = review.link_choices if review is not None else {}
        selectable, resolved, conflicts = set(), {}, []
        for release in request.releases:
            for incoming in release.links:
                pair = release.key, incoming.item_key
                old = stored.get((release_matches[release.key], item_matches[incoming.item_key]))
                chosen = incoming
                if old is not None:
                    selectable.add(pair)
                    current = ReplacementLink(item_key=incoming.item_key,
                        item_state=type(incoming.item_state)(old["item_state"]),
                        target_scope=type(incoming.target_scope)(old["target_scope"]),
                        target_group_ids=tuple(old["target_group_ids"] or ()))
                    different = (current.item_state != incoming.item_state or current.target_scope != incoming.target_scope
                                 or set(current.target_group_ids) != set(incoming.target_group_ids))
                    choice = choices.get(pair)
                    if different and choice is None:
                        conflicts.append(LinkConflict(release.key, incoming.item_key, current, incoming))
                    elif choice == "CURRENT":
                        chosen = current
                resolved[pair] = chosen
        require(set(choices) <= selectable, "기존 연결이 없는 릴리즈·Item의 값을 선택할 수 없습니다.")
        return tuple(conflicts), resolved

    def _risk_reconnections(self, request, state, release_matches, item_matches, review):
        """새 항목 키로 수동 위험의 복원 대상을 제안하고 근거·범위를 검증한다.

        DB에 쓰지 않으며 선택한 CURRENT 값도 최종 후보에 반영한다. 누락된 해제
        경계와 원래 미지정한 해제 경계를 구분하고, 순서를 추정하지 않는다.

        param
        - request: 전체 노트의 새 원본·검토값·포함 관계.
        - state: 기존 릴리즈·Item·위험·원본과 명시 연결.
        - release_matches, item_matches: 새 항목 키별 동일한 기존 ID 또는 신규 None.
        - review: 선택한 매칭·충돌값. None이면 새 입력값으로 후보를 구성.

        return
        - 위험 ID별 복원 후보·원본 근거·확인 필요 여부 튜플.
        """
        old_releases = {row["release_version_id"]: row for row in state["releases"]}
        old_items = {row["item_id"]: row for row in state["items"]}
        risk_items = {row["item_id"]: row for row in (*state["items"], *state["risk_items"])}
        batches = {row["batch_id"]: row["raw_data"] for row in state["batches"]}
        final_releases = {entry.key: self._resolved_values("release", entry, release_matches[entry.key],
                                                        old_releases, review) for entry in request.releases}
        release_keys = {old_id: key for key, old_id in release_matches.items() if old_id is not None}
        item_keys = {old_id: key for key, old_id in item_matches.items() if old_id is not None}
        item_numbers = {entry.key: self._number(self._source(request.raw_data, asdict(entry.value), "item_no"))
                        for entry in request.items}
        original_versions = {entry.key: self._source(request.raw_data, asdict(entry.value), "version_raw")
                             for entry in request.releases}
        memberships = defaultdict(list)
        for link in state["links"]:
            release = old_releases.get(link["release_version_id"])
            item = old_items.get(link["item_id"])
            if release is not None and item is not None:
                memberships[item["item_id"]].append({"family_id": request.family_id,
                    "version_raw": self._source(batches[release["batch_id"]], release, "version_raw"),
                    "item_no": self._number(self._source(batches[item["batch_id"]], item, "item_no"))})
        known_orders = defaultdict(int)
        for values in final_releases.values():
            if values["version_order"] is not None:
                known_orders[values["version_order"]] += 1
        fields = (("release_version_id", "direct_version"), ("start_release_version_id", "start_version"),
                  ("end_release_version_id", "end_version"))
        proposals = []
        for rule in state["risks"]:
            saved = deepcopy(rule["target_reference"])
            proposed = {field: None for field, _ in fields}
            proposed["item_id"] = None
            required = ({"release_version_id"} if rule["rule_type"] == "DIRECT_VERSION" else
                        {"item_id", "start_release_version_id"} if rule["rule_type"] == "DEFECT_ITEM" else {"item_id"})
            confirm = rule["mapping_status"] != "VERIFIED"
            for field, evidence_key in fields:
                old_id = rule[field]
                if old_id is not None:
                    required.add(field)
                    proposed[field] = release_keys.get(old_id)
                    old_release = old_releases.get(old_id)
                    if old_release is not None:
                        saved[evidence_key] = {name: deepcopy(old_release[name]) for name in
                            ("release_version_id", "family_id", "version_raw", "version_suffix")}
                        original_raw = self._source(batches[old_release["batch_id"]], old_release, "version_raw")
                        if isinstance(original_raw, str) and original_raw.strip():
                            saved[evidence_key]["original_version_raw"] = original_raw
                        new_key = proposed[field]
                        if new_key is not None and final_releases[new_key]["version_raw"] != old_release["version_raw"]:
                            confirm = True
                elif evidence_key in saved:
                    required.add(field)
                    confirm = True
                    evidence = saved[evidence_key]
                    candidates = []
                    if isinstance(evidence, dict) and evidence.get("family_id") == request.family_id:
                        original_raw = evidence.get("original_version_raw")
                        for key, values in final_releases.items():
                            matches = (original_versions[key] == original_raw if original_raw is not None
                                       else values["version_raw"] == evidence.get("version_raw"))
                            if matches:
                                candidates.append(key)
                    if len(candidates) == 1:
                        proposed[field] = candidates[0]
            old_item_id = rule["item_id"]
            if old_item_id is not None:
                required.add("item_id")
                proposed["item_id"] = item_keys.get(old_item_id)
                if old_item_id in risk_items:
                    saved["item"] = {**deepcopy(risk_items[old_item_id]), "family_id": request.family_id,
                                     "memberships": memberships[old_item_id]}
            elif "item" in saved:
                required.add("item_id")
                confirm = True
                evidence = saved["item"]
                candidates = set()
                if isinstance(evidence, dict):
                    original_memberships = evidence.get("memberships", ())
                    for membership in original_memberships if isinstance(original_memberships, (tuple, list)) else ():
                        if (not isinstance(membership, dict) or membership.get("family_id") != request.family_id
                                or membership.get("version_raw") is None or membership.get("item_no") is None):
                            continue
                        for entry in request.releases:
                            if original_versions[entry.key] == membership["version_raw"]:
                                candidates.update(link.item_key for link in entry.links
                                                  if item_numbers[link.item_key] == membership["item_no"])
                if len(candidates) == 1:
                    proposed["item_id"] = next(iter(candidates))
            missing = sorted(field for field in required if proposed[field] is None)
            reason = "위험 대상 또는 원래 알려진 해제 경계를 유일하게 확인할 수 없습니다." if missing else None
            endpoints = [proposed[field] for field in ("start_release_version_id", "end_release_version_id")
                         if proposed[field] is not None]
            if any(final_releases[key]["version_order"] is None
                   or known_orders[final_releases[key]["version_order"]] != 1 for key in endpoints):
                reason = "시작·해제 버전의 순서가 미확인 또는 중복입니다. 순서를 확인해 주세요."
            start, end = proposed["start_release_version_id"], proposed["end_release_version_id"]
            if start is not None and end is not None:
                start_order, end_order = final_releases[start]["version_order"], final_releases[end]["version_order"]
                if start_order is not None and end_order is not None and start_order >= end_order:
                    reason = "해제 버전은 시작 버전보다 뒤여야 합니다. 위험 범위를 다시 확인해 주세요."
            can_restore = reason is None
            if can_restore and confirm:
                reason = "미확인 연결 또는 변경된 버전 원문과 복원 후보가 같은 대상인지 확인해 주세요."
            proposals.append(RiskReconnection(rule["risk_rule_id"], saved, proposed, can_restore, confirm, reason))
        return tuple(proposals)

    @staticmethod
    def _resolved_values(kind, entry, old_id, old_rows, review):
        """선택한 기존값을 반영해 저장 전 최종 후보를 구성한다.

        param
        - kind, entry: release 또는 item 구분과 새 요청 항목.
        - old_id, old_rows: 동일 항목의 기존 ID와 현재 관리값.
        - review: 사용자가 선택한 충돌값. None이면 새 입력값 사용.

        return
        - 원본이나 요청을 변경하지 않은 최종 후보 값.
        """
        values = asdict(entry.value)
        if review is not None and old_id is not None:
            for (choice_kind, key, name), choice in review.choices.items():
                if choice_kind == kind and key == entry.key and choice == "CURRENT":
                    values[name] = deepcopy(old_rows[old_id][name])
        return values

    @staticmethod
    def _json_value(value):
        """검토 지문에서 날짜를 원문 손실 없이 표현한다.

        param
        - value: JSON 기본 형식이 아닌 값.

        return
        - 날짜·시각의 ISO 문자열.

        raises
        - TypeError: 지원하지 않는 형식인 경우.
        """
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        raise TypeError("검토 지문에 지원하지 않는 값이 있습니다.")

    @staticmethod
    def _number(value):
        """원본 셀의 명확한 정수 번호만 매칭 키로 사용한다.

        param
        - value: 원본 번호 셀 값.

        return
        - 확인한 정수. Excel의 정수형 실수도 허용하며 불리언·소수·문자 혼합·빈 값은 None.
        """
        if type(value) is int:
            return value if -2147483648 <= value <= 2147483647 else None
        if type(value) is float and value.is_integer() and -2147483648 <= value <= 2147483647:
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            digits = text[1:] if text.startswith("-") else text
            if text.isascii() and digits.isdecimal():
                try:
                    number = int(text)
                except ValueError:
                    return None
                return number if -2147483648 <= number <= 2147483647 else None
        return None

    def _chosen(self, kind, entry, old_id, old_rows, review, batch_id):
        """사용자의 값 선택을 반영하고 새 출처와 수정 표시를 구성한다.

        param
        - kind: release 또는 item.
        - entry: 새 입력 항목.
        - old_id: 확인한 기존 항목 ID. 신규는 None.
        - old_rows: 기존 ID별 관리값.
        - review: 명시적인 값 선택.
        - batch_id: 새 원본 입력 작업 ID.

        return
        - 현재값 선택과 새 원본 출처를 포함한 저장값.
        """
        values = prepare_record(replace(entry.value, batch_id=batch_id))
        edited = list(values["edited_fields"])
        for (choice_kind, key, name), choice in review.choices.items():
            if choice_kind == kind and key == entry.key and choice == "CURRENT":
                require(old_id is not None, "신규 항목에는 기존값을 선택할 수 없습니다.")
                values[name] = deepcopy(old_rows[old_id][name])
                if name not in edited:
                    edited.append(name)
        values["edited_fields"] = edited
        return values

    def _apply(self, tx, request, state, preview, review):
        """동일 항목 ID·공유 Item·위험 지정을 보존하며 전체 교체한다.

        param
        - tx: 모든 변경·판정이 공유하는 트랜잭션.
        - request: 전체 계열 입력.
        - state: 잠근 기존 관리값과 관계.
        - preview: 선택 완료 검토 결과.
        - review: 사용자 선택.

        return
        - 새 계열의 릴리즈 상세 튜플.
        """
        batch = tx.insert("import_batch", {"source_type": "EXCEL", "raw_data": request.raw_data})
        old_releases = {r["release_version_id"]: r for r in state["releases"]}
        old_items = {r["item_id"]: r for r in state["items"]}
        link_conflicts, resolved_links = self._review_links(request, state, preview.release_matches,
                                                          preview.item_matches, review)
        require(not link_conflicts, "Item 포함 상태·모델 범위의 충돌 선택이 필요합니다.")
        family_ids = set(old_releases)
        outside_items = {r["item_id"] for r in state["links"] if r["release_version_id"] not in family_ids}
        links = tx.repository.table("release_version_item")
        if family_ids:
            tx.session.execute(links.delete().where(links.c.release_version_id.in_(family_ids)))
        item_ids, release_ids = {}, {}
        release_map = {}
        for entry in request.items:
            old_id = preview.item_matches[entry.key]
            values = self._chosen("item", entry, old_id, old_items, review, batch["batch_id"])
            if old_id is not None and old_id not in outside_items:
                row = tx.update("item", {"item_id": old_id}, values)
                self.releases.repository.view(tx, "item", row)
            else:
                row = self.releases._insert(tx, "item", values, {})
            item_ids[entry.key] = row["item_id"]
        retained = {i for i in preview.release_matches.values() if i is not None}
        for old_id in sorted(family_ids - retained):
            tx.delete_release_version(old_id)
        release_values = {entry.key: self._chosen("release", entry, preview.release_matches[entry.key],
                                                old_releases, review, batch["batch_id"])
                          for entry in request.releases}
        self._stage_release_names(tx, old_releases, preview.release_matches, release_values)
        for entry in request.releases:
            old_id = preview.release_matches[entry.key]
            values = release_values[entry.key]
            if values["release_note_id"] is not None:
                note = tx.get("release_note", {"release_note_id": values["release_note_id"]})
                family = tx.get("version_family", {"family_id": request.family_id})
                owner = tx.get("equipment_group", {"group_id": family["group_id"]})
                note_owner = tx.get("equipment_group", {"group_id": note["group_id"]})
                require(owner["category"] == note_owner["category"], "릴리즈 노트와 계열의 설비군이 다릅니다.")
            if old_id is not None:
                row = tx.update("release_version", {"release_version_id": old_id}, values)
                self.releases.repository.view(tx, "release_version", row)
            else:
                row = self.releases._insert(tx, "release_version", values, {})
            release_ids[entry.key] = row["release_version_id"]
            if old_id is not None:
                release_map[old_id] = row["release_version_id"]
            for link in entry.links:
                selected = resolved_links[(entry.key, link.item_key)]
                self.releases._link(tx, row, ItemLinkInput(item_id=item_ids[link.item_key], item_state=selected.item_state,
                    target_scope=selected.target_scope, target_group_ids=selected.target_group_ids))
        for old_id in sorted(set(old_items) - set(item_ids.values()) - outside_items):
            tx.delete_release_item(old_id)
        self._reconnect_risks(tx, state, preview, release_ids, item_ids, review.risk_confirmations)
        # 기존 확인 ID라도 버전 관리값 자체가 바뀌면 원문·확정 별칭으로 다시 검증한다.
        renamed = {i for i, row in old_releases.items() if i in release_map and
                   tx.get("release_version", {"release_version_id": i})["version_raw"] != row["version_raw"]}
        if renamed:
            versions = self._rows(tx, "equipment_version", True, family_id=request.family_id)
            for version in versions:
                if version["release_version_id"] in renamed:
                    tx.update("equipment_version", {"equipment_version_id": version["equipment_version_id"]},
                              {"release_version_id": None})
        return tuple(self.releases._detail(tx, tx.get("release_version", {"release_version_id": release_ids[e.key]}))
                     for e in request.releases)

    def _stage_release_names(self, tx, old_releases, matches, final_values):
        """유일한 최종 버전명끼리의 순환 교환을 같은 트랜잭션 안에서 준비한다.

        바뀌는 기존 행만 충돌하지 않는 임시 원문으로 옮겨 새 이름을 모두 비운다.
        다음 저장 단계가 이를 최종값으로 덮으며 실패하면 임시값까지 전부 취소된다.
        출처·수정 표시·위험 근거에는 임시 원문을 기록하지 않는다.

        param
        - tx: 전체 교체와 공유하는 트랜잭션.
        - old_releases: 교체 전 릴리즈 ID별 관리값.
        - matches: 새 릴리즈 키별 유지할 기존 ID 또는 신규 None.
        - final_values: 사용자 선택을 반영한 최종 릴리즈 값.

        raises
        - StorageValidationError: 최종 버전 원문이 같은 계열 안에서 중복인 경우.
        """
        names = [values["version_raw"] for values in final_values.values()]
        require(len(names) == len(set(names)), "같은 계열의 최종 릴리즈 버전 원문이 중복되었습니다.")
        occupied = set(names) | {row["version_raw"] for row in old_releases.values()}
        for key, values in final_values.items():
            old_id = matches[key]
            if old_id is None or old_releases[old_id]["version_raw"] == values["version_raw"]:
                continue
            placeholder = f"__setron_replace_{uuid4().hex}__"
            while placeholder in occupied:
                placeholder = f"__setron_replace_{uuid4().hex}__"
            occupied.add(placeholder)
            tx.update("release_version", {"release_version_id": old_id}, {"version_raw": placeholder})

    def _reconnect_risks(self, tx, state, preview, release_ids, item_ids, confirmations):
        """검토한 제안만 실제 ID로 바꾸고 수동 위험의 근거·미확인을 보존한다.

        param
        - tx: 교체 트랜잭션.
        - state: 교체 전 위험과 릴리즈 관리값.
        - preview: 같은 DB·요청·값 선택으로 재검증한 위험 복원 제안.
        - release_ids, item_ids: 저장한 새 요청 키별 실제 ID.
        - confirmations: 복원 가능한 제안을 명시 확인한 위험 ID 튜플.
        """
        old_rules = {row["risk_rule_id"]: row for row in state["risks"]}
        old_releases = {row["release_version_id"]: row for row in state["releases"]}
        unlinked_items = {row["item_id"] for row in state["risk_items"]}
        for proposal in preview.risk_reconnections:
            old = old_rules[proposal.risk_rule_id]
            confirmed = proposal.risk_rule_id in confirmations
            restore = proposal.can_restore and (not proposal.requires_confirmation or confirmed)
            changes = {}
            for field, key in proposal.proposed_targets.items():
                selected = (item_ids if field == "item_id" else release_ids).get(key)
                if field == "item_id" and selected is None and old[field] in unlinked_items:
                    # 교체 원본에 없는 수동 지정은 기존 연결과 미확인 상태를 그대로 보존한다.
                    selected = old[field]
                # 확인하지 않은 새 후보나 변경된 버전 원문에 수동 위험을 전파하지 않는다.
                if not restore and selected is not None:
                    if old[field] is None:
                        selected = None
                    elif field != "item_id":
                        before = old_releases.get(old[field])
                        after = tx.get("release_version", {"release_version_id": selected})
                        if before is None or before["version_raw"] != after["version_raw"]:
                            selected = None
                changes[field] = selected
            changes.update(target_reference=deepcopy(proposal.target_reference),
                           mapping_status="VERIFIED" if restore else "UNRESOLVED")
            tx.update("risk_rule", {"risk_rule_id": proposal.risk_rule_id}, changes)
