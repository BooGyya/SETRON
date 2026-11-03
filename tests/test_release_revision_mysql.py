"""실제 MySQL 5.6.19에서 릴리즈 변경과 관련 판정의 원자성을 검증한다."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Event

import pytest

import test_release_revision_facade as cases
from setron.features.release.replacement import FamilyReplacementService
from setron.features.release.replacement_schemas import ReplacementRelease
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ReleaseInput
from test_release_replacement import original, request, reviewed, source
from test_release_replacement_mysql import revision_mysql_storage
from test_release_revision import all_rows, observation
from test_schema_mysql import schema_database

pytestmark = pytest.mark.skipif(
    not os.environ.get("SETRON_TEST_DB_NAME"), reason="MySQL 전용 테스트 DB 접속 설정이 필요합니다.",
)


def test_mysql_registration_relinks_existing_history_without_changing_original(revision_mysql_storage):
    """실제 MySQL에서 새 릴리즈 등록이 여러 과거 관측을 연결하고 원본을 보존하는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    """
    cases.test_register_release_updates_existing_unregistered_history(revision_mysql_storage)


def test_mysql_rename_invalidates_unconfirmed_old_version_link(revision_mysql_storage):
    """실제 MySQL에서 이름 수정 후 이전 원문을 새 버전으로 단정하지 않는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    """
    cases.test_release_rename_rechecks_old_correspondence_without_changing_original(revision_mysql_storage)


def test_mysql_confirmed_alias_preserves_link_after_rename(revision_mysql_storage):
    """명시적인 표기 대응이 있는 이름 수정은 MySQL에서도 기존 연결을 유지하는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    """
    cases.test_confirmed_alias_can_preserve_correspondence_after_rename(revision_mysql_storage)


def test_mysql_item_state_changes_recalculate_application_and_safety_risk(revision_mysql_storage):
    """MySQL의 Item 연결 갱신이 적용 상태와 환경·안전 위험을 함께 바꾸는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    """
    cases.test_saved_item_scope_and_state_recalculate_item_and_risk_together(revision_mysql_storage)


def test_mysql_shared_item_edit_recalculates_all_explicit_families(revision_mysql_storage):
    """MySQL의 공유 Item 잠금·수정 후 연결된 두 계열 모두 최신값으로 판정하는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    """
    cases.test_shared_item_edit_updates_all_linked_families(revision_mysql_storage)


def test_mysql_refresh_failure_rolls_back_new_release_items_and_batch(revision_mysql_storage, monkeypatch):
    """MySQL에서 재판정 실패 시 입력 작업·릴리즈·Item·연결 전체가 취소되는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    - monkeypatch: 저장 이후 재판정 실패를 주입하는 pytest 도구.
    """
    cases.test_recalculation_failure_rolls_back_release_and_new_items(revision_mysql_storage, monkeypatch)


def test_mysql_refresh_failure_restores_previous_release_values(revision_mysql_storage, monkeypatch):
    """MySQL에서 수정 이후 재판정 실패 시 기존 관리값·수정 이력을 복구하는지 검증한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    - monkeypatch: 수정 이후 재판정 실패를 주입하는 pytest 도구.
    """
    cases.test_edit_recalculation_failure_restores_previous_management_values(revision_mysql_storage, monkeypatch)


def test_mysql_edit_sees_replacement_committed_after_family_discovery(revision_mysql_storage, monkeypatch):
    """계열 사전 확인 직후 다른 작업이 교체해도 수정 판정이 최신 릴리즈 전체를 읽는지 검증한다.

    MySQL REPEATABLE READ에서 사전 조회의 읽기 시점이 쓰기 업무까지 이어지면
    새 v2를 보지 못한다. 사전 조회 종료 후 교체를 확정하고 수정을 진행해 이를 구분한다.

    param
    - revision_mysql_storage: 이번 검증 전용 MySQL 업무 저장소.
    - monkeypatch: 사전 확인과 쓰기 사이에 동시 교체를 배치하는 pytest 도구.
    """
    storage = revision_mysql_storage
    initial = original(storage)
    release_id = initial.release.current["release_version_id"]
    observation(storage, versions=((1, 1, "v2", None),))
    service = ReleaseRevisionService(storage)
    discover = service._discover_release_family
    discovered, proceed = Event(), Event()

    def pause_after_discovery(identifier):
        """읽기 업무를 끝낸 뒤 교체가 확정될 때까지 쓰기 시작을 보류한다.

        param
        - identifier: 수정할 기존 릴리즈 ID.

        return
        - 읽기 업무에서 확인한 계열 ID.
        """
        family = discover(identifier)
        discovered.set()
        assert proceed.wait(15), "동시 교체가 정해진 시간 안에 끝나지 않았습니다."
        return family

    monkeypatch.setattr(service, "_discover_release_family", pause_after_discovery)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(service.update_release, release_id, {"released_at": date(2026, 10, 3)})
        try:
            assert discovered.wait(10), "수정 작업의 계열 확인이 정해진 시간 안에 끝나지 않았습니다."
            current = request()
            new_release = ReplacementRelease(key="new", value=ReleaseInput(
                family_id=1, version_raw="v2", version_order=2,
                source_reference={**source("version_raw", "A2"), **source("version_order", "B2")},
            ))
            current = replace(current, releases=(*current.releases, new_release), raw_data={
                "노트": {**current.raw_data["노트"], "A2": "v2", "B2": 2},
            })
            replacement = FamilyReplacementService(storage)
            details, _ = replacement.replace(current, reviewed(replacement, current))
            new_id = next(detail.release.current["release_version_id"] for detail in details
                          if detail.release.current["version_raw"] == "v2")
        finally:
            proceed.set()
        result = pending.result(timeout=20)
    assert result.refresh.families[1].get(new_id).version_raw == "v2"
    assert all_rows(storage, "equipment_version")[0]["release_version_id"] == new_id
    assert result.value.release.current["released_at"] == date(2026, 10, 3)
