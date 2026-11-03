"""N 후보 확인과 기존 BE-004 Item 저장의 잠금 순서를 실제 MySQL에서 검증한다."""

from concurrent.futures import ThreadPoolExecutor
import os
import re
from threading import Event, local
from uuid import uuid4

import pytest
from sqlalchemy import event

from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.targets.schemas import CandidateConfirmation
from setron.features.targets.service import TargetService

from test_schema_mysql import schema_database, storage


pytestmark = pytest.mark.skipif(not os.environ.get("SETRON_TEST_DB_NAME"),
                                reason="MySQL 전용 테스트 DB 설정이 필요합니다.")


@pytest.mark.parametrize("first", ("links", "confirm"))
def test_confirm_and_item_scope_update_both_commit_in_either_lock_order(storage, first):
    """첫 작업이 릴리즈를 잠근 뒤 두 번째 작업이 같은 잠금을 시도하게 한다.

    첫 작업은 두 번째 작업의 SQL 실행 *직전* 이벤트까지만 기다린다. 따라서
    릴리즈→모델 순서가 같으면 자연스럽게 직렬화된다. 모델→릴리즈였던 구현은
    links가 먼저 릴리즈를 잠근 사례에서 서로 기다려 오류 1213으로 실패한다.
    """
    suffix = uuid4().hex[:10]
    with storage.transaction() as tx:
        model = tx.insert("equipment_group", {
            "group_name": f"BE011 동시성 {suffix}", "category": "POLY",
        })["group_id"]
        family = tx.insert("version_family", {
            "group_id": model, "family_name": f"BE011 동시성 {suffix}",
        })["family_id"]
    releases = ReleaseService(storage)
    original = releases.register_release(
        ReleaseInput(family_id=family, version_raw="v1", version_order=1),
        (ItemInput(content="보존할 Item 내용"),),
    )
    release_id = original.release.current["release_version_id"]
    item_id = original.items[0].item.current["item_id"]
    newer = releases.register_release(ReleaseInput(family_id=family, version_raw="v2_n4", version_order=2))
    newer_id = newer.release.current["release_version_id"]
    candidates = tuple(CandidateConfirmation(
        rid, "합성 시험의 모델 적용 확인", "합성 시험의 같은 분기 순서 확인",
        source_text="BE-011 MySQL 동시성 회귀검증",
    ) for rid in (release_id, newer_id))
    TargetService(storage).confirm(model, family, candidates[:1])
    original_item = releases.get_item(item_id).current

    held, second_attempted = Event(), Event()
    thread_state = local()
    engine = storage.database.engine

    def release_lock_query(statement):
        normalized = statement.replace("`", "")
        return "FOR UPDATE" in normalized.upper() and re.search(
            r"\bFROM\s+release_version\b", normalized, re.IGNORECASE,
        ) is not None

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        if getattr(thread_state, "role", None) not in (None, first) and release_lock_query(statement):
            second_attempted.set()

    def after_execute(connection, cursor, statement, parameters, context, executemany):
        if (getattr(thread_state, "role", None) == first
                and not getattr(thread_state, "gated", False) and release_lock_query(statement)):
            thread_state.gated = True
            held.set()
            assert second_attempted.wait(10), "두 번째 작업이 릴리즈 잠금을 시도하지 않았습니다."

    def run(role):
        thread_state.role = role
        try:
            if role != first:
                assert held.wait(10), "첫 작업이 릴리즈 잠금을 확보하지 못했습니다."
            if role == "confirm":
                return TargetService(storage).confirm(model, family, candidates)
            return releases.save_item_links(release_id, (ItemLinkInput(
                item_id=item_id, item_state=ItemState.NOT_APPLIED,
                target_scope=TargetScope.LISTED, target_group_ids=(model,),
            ),))
        finally:
            thread_state.role = None

    event.listen(engine, "before_cursor_execute", before_execute)
    event.listen(engine, "after_cursor_execute", after_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, role) for role in (first, "confirm" if first == "links" else "links")]
            for future in futures:
                future.result(timeout=20)
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)
        event.remove(engine, "after_cursor_execute", after_execute)

    assert held.is_set() and second_attempted.is_set()
    confirmed = TargetService(storage).read(((model, family),)).pairs[0]
    assert tuple(candidate.release_id for candidate in confirmed.candidates) == (release_id, newer_id)
    assert tuple(candidate.confirmation for candidate in confirmed.candidates) == candidates
    assert confirmed.recommended_release_id == newer_id and confirmed.selection_valid
    saved = releases.get_release(release_id)
    assert saved.items[0].item_state is ItemState.NOT_APPLIED
    assert saved.items[0].target_scope is TargetScope.LISTED
    assert saved.items[0].target_group_ids == (model,)
    assert releases.get_item(item_id).current == original_item
