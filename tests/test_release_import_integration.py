"""실제 가져오기 저장 경로의 릴리즈 전체 교체·충돌 검토·전체 취소 검증."""

from dataclasses import replace

import pytest

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.schemas import SaveRequest, SaveRow
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.replacement_schemas import ReplacementReview, ReplacementReviewRequired
from setron.features.release.replacement import FamilyReplacementService
from setron.features.release.schemas import ItemLinkInput
from setron.features.release.service import ReleaseService
from setron.features.release.schemas import ReleaseInput
from setron.features.storage.validation import StorageValidationError

from test_release_revision import all_rows, observation, revision_storage
from test_release_replacement import original


def import_request(storage, *, identity="import", raw="v1", content="원본 내용", confirmed=True, sheet="노트"):
    """등록 모델과 실제 시트·셀 위치를 가진 가져오기 요청을 만든다.

    param
    - storage: 테스트 업무 DB.
    - identity, raw, content, confirmed, sheet: 요청 ID·버전·내용·전체 노트 확인·시트명.

    return
    - 실제 DatabaseImportSaver가 처리하는 SaveRequest.
    """
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
    values = {"group_name": "OXIDE", "model_name": "M-O2", "family_name": "O2", "version_raw": raw,
              "item_no": 7, "content": content}
    sources = {field: {"sheet": sheet, "cells": [cell]}
               for field, cell in (("version_raw", "A1"), ("model_name", "B1"), ("item_no", "C1"),
                                   ("content", "D1"), ("family_name", "E1"), ("group_name", "F1"))}
    return SaveRequest(identity, ExtractKind.RELEASE_NOTE, "검토한.xlsx",
        {"sheet": sheet, "cells": {"A1": raw, "B1": "M-O2", "C1": 7, "D1": content, "E1": "O2", "F1": "Oxide"}},
        (SaveRow((1,), values, sources, (), family_id=1),), 0, None,
        release_note_confirmed=confirmed, filename_confirmed=confirmed)


def reviewed_request(saver, request, *, incoming=True):
    """실제 저장 직전의 모든 레코드·포함 관계 충돌을 명시적으로 선택한다.

    param
    - saver, request: 실제 저장 서비스와 검토 요청.
    - incoming: True이면 새 자료, False이면 현재 관리값 선택.

    return
    - 계열별 검토 결과를 보관한 SaveRequest.
    """
    plans = saver.preview_release_import(request)
    reviews = []
    for plan in plans:
        p = plan.preview
        reviews.append(ReplacementReview(fingerprint=p.fingerprint,
            choices={(c.kind, c.key, c.field): "INCOMING" if incoming else "CURRENT" for c in p.conflicts},
            link_choices={(c.release_key, c.item_key): "INCOMING" if incoming else "CURRENT" for c in p.link_conflicts}))
    return replace(request, release_reviews=tuple(reviews))


def test_real_import_replacement_preserves_selected_manual_link_and_refreshes_history(revision_storage):
    """실제 가져오기 경로에서 수동 Item 상태 선택과 과거 관측 재판정을 함께 저장하는지 확인한다.

    param
    - revision_storage: 실제 트랜잭션의 독립적인 업무 DB.
    """
    storage = revision_storage
    first = original(storage)
    rid, iid = first.release.current["release_version_id"], first.items[0].item.current["item_id"]
    ReleaseService(storage).save_item_links(rid, (ItemLinkInput(item_id=iid, item_state=ItemState.NOT_APPLIED,
        target_scope=TargetScope.LISTED, target_group_ids=(1,)),))
    observation(storage, versions=((1, 1, "v1", rid),))
    saver = DatabaseImportSaver(storage)
    request = import_request(storage)
    plan, = saver.preview_release_import(request)
    assert plan.preview.link_conflicts
    with pytest.raises(ReplacementReviewRequired):
        saver.save(request)
    receipt = saver.save(reviewed_request(saver, request, incoming=False))
    link = all_rows(storage, "release_version_item")[0]
    assert (link["item_state"], link["target_scope"], link["target_group_ids"]) == ("NOT_APPLIED", "LISTED", [1])
    assert receipt.refresh is not None and len(receipt.refresh.dates) == 1
    assert all_rows(storage, "equipment_version")[0]["version_raw"] == "v1"


def test_real_import_existing_release_requires_explicit_full_note_confirmation(revision_storage):
    """기존 릴리즈 전체 교체를 명시적인 누적 노트 확인 없이 실행하지 않는지 검증한다.

    param
    - revision_storage: 독립적인 검증 DB.
    """
    storage = revision_storage
    original(storage)
    saver = DatabaseImportSaver(storage)
    request = import_request(storage, confirmed=False)
    before = all_rows(storage, "release_version")
    with pytest.raises(StorageValidationError, match="전체 누적 노트"):
        saver.save(request)
    assert all_rows(storage, "release_version") == before


def test_real_import_stale_preview_aborts_before_any_source_write(revision_storage):
    """교체 검토 후 관리값이 바뀌면 원본 입력 작업도 추가하지 않는지 확인한다.

    param
    - revision_storage: 독립적인 검증 DB.
    """
    storage = revision_storage
    first = original(storage)
    saver = DatabaseImportSaver(storage)
    request = reviewed_request(saver, import_request(storage))
    ReleaseService(storage).update_item(first.items[0].item.current["item_id"], {"content": "동시 수정"})
    batches = all_rows(storage, "import_batch")
    with pytest.raises(StorageValidationError, match="검토 후"):
        saver.save(request)
    assert all_rows(storage, "import_batch") == batches


def test_real_import_new_release_relinks_existing_unregistered_history(revision_storage):
    """기존의 신규 릴리즈 추가 경로도 과거 미등록 SW 연결을 같은 저장에서 갱신하는지 확인한다.

    param
    - revision_storage: 독립적인 검증 DB.
    """
    storage = revision_storage
    observation(storage)
    saver = DatabaseImportSaver(storage)
    receipt = saver.save(import_request(storage, confirmed=False))
    row = all_rows(storage, "equipment_version")[0]
    assert row["release_version_id"] is not None and row["version_raw"] == "v1"
    assert receipt.refresh.dates[0].summary.equipment[0].families[0].pms[0].comparison.registered


def test_whole_note_removes_omitted_versions_and_preserves_manual_risk(revision_storage):
    """가져오기에서 빠진 버전을 제외하되 위험 지정과 관측 원문은 미확인으로 보존하는지 확인한다.

    param
    - revision_storage: 독립적인 검증 DB.
    """
    storage = revision_storage
    original(storage)
    removed = original(storage, raw="v2", number=8, order=2)
    old_id = removed.release.current["release_version_id"]
    observation(storage, versions=((1, 1, "v2", old_id),))
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1, "release_version_id": old_id,
            "risk_reason": "직접 지정", "target_reference": {}, "mapping_status": "VERIFIED"})
    saver = DatabaseImportSaver(storage)
    receipt = saver.save(reviewed_request(saver, import_request(storage)))
    assert [r["version_raw"] for r in all_rows(storage, "release_version")] == ["v1"]
    assert all_rows(storage, "equipment_version")[0]["version_raw"] == "v2"
    assert all_rows(storage, "risk_rule")[0]["mapping_status"] == "UNRESOLVED"
    assert receipt.refresh.unresolved_risk_ids


def test_same_family_multi_sheet_import_is_one_reviewed_cumulative_note(revision_storage):
    """여러 시트의 같은 계열을 한 번에 교체하고 각 시트 출처를 보존하는지 확인한다.

    param
    - revision_storage: 독립적인 검증 DB.
    """
    storage = revision_storage
    original(storage)
    saver = DatabaseImportSaver(storage)
    first = import_request(storage, identity="one", sheet="앞부분")
    second = import_request(storage, identity="two", raw="v2", sheet="뒷부분", content="새 버전 내용")
    requests = first, second
    plans = saver.preview_release_import(requests)
    assert len(plans) == 1 and len(plans[0].request.releases) == 2
    reviews = tuple(ReplacementReview(fingerprint=p.preview.fingerprint,
        link_choices={(c.release_key, c.item_key): "CURRENT" for c in p.preview.link_conflicts}) for p in plans)
    receipts = saver.save_many(tuple(replace(r, release_reviews=reviews) for r in requests))
    assert receipts[0].batch_id == receipts[1].batch_id
    batch = next(row for row in all_rows(storage, "import_batch") if row["batch_id"] == receipts[0].batch_id)
    assert set(batch["raw_data"]) == {"앞부분", "뒷부분"}
    assert {r["version_raw"] for r in all_rows(storage, "release_version")} == {"v1", "v2"}


def test_shared_item_identity_is_preserved_across_reviewed_cumulative_versions(revision_storage):
    """기존의 같은 Item을 공유하는 두 버전을 원본 번호만으로 별개 Item으로 복제하지 않는지 확인한다.

    param
    - revision_storage: 독립적인 검증 DB.
    """
    storage = revision_storage
    first = original(storage)
    second = ReleaseService(storage).register_release(ReleaseInput(family_id=1, version_raw="v2", version_order=2))
    item_id = first.items[0].item.current["item_id"]
    ReleaseService(storage).save_item_links(second.release.current["release_version_id"],
        (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED),))
    saver = DatabaseImportSaver(storage)
    a = import_request(storage, identity="a", sheet="첫노트")
    b = import_request(storage, identity="b", raw="v2", sheet="둘째노트")
    plans = saver.preview_release_import((a, b))
    assert len(plans[0].request.items) == 1
    review = ReplacementReview(fingerprint=plans[0].preview.fingerprint)
    saver.save_many((replace(a, release_reviews=(review,)), replace(b, release_reviews=(review,))))
    assert len(all_rows(storage, "item")) == 1
    assert {row["item_id"] for row in all_rows(storage, "release_version_item")} == {item_id}


def test_final_recalculation_failure_rolls_back_actual_import_and_allows_retry(revision_storage, monkeypatch):
    """실제 저장의 교체 후 재판정 실패를 모두 취소하고 같은 요청을 검증 후 재시도할 수 있는지 확인한다.

    param
    - revision_storage: 독립적인 검증 DB.
    - monkeypatch: 재판정 실패 주입 도구.
    """
    from setron.features.release.refresh import ReleaseRefreshService
    storage = revision_storage
    original(storage)
    saver = DatabaseImportSaver(storage)
    request = reviewed_request(saver, import_request(storage))
    before = {name: all_rows(storage, name) for name in ("release_version", "item", "import_batch", "release_version_item")}
    real = ReleaseRefreshService.refresh_in

    def fail(*args, **kwargs):
        """교체 뒤 재판정 단계에서 오류를 재현한다.

        raises
        - StorageValidationError: 검증용 실패.
        """
        raise StorageValidationError("재판정 실패")

    monkeypatch.setattr(ReleaseRefreshService, "refresh_in", fail)
    with pytest.raises(StorageValidationError, match="재판정 실패"):
        saver.save(request)
    assert {name: all_rows(storage, name) for name in before} == before
    monkeypatch.setattr(ReleaseRefreshService, "refresh_in", real)
    assert saver.save(request).refresh is not None


@pytest.mark.parametrize("fail_second", (False, True))
def test_two_family_notes_sharing_original_batch_and_item_are_atomic(revision_storage, monkeypatch, fail_second):
    """원본 배치·Item을 공유하던 두 계열의 전체 교체와 후속 실패 취소를 검증한다.

    param
    - revision_storage: 독립적인 업무 DB.
    - monkeypatch: 두 번째 계열 저장 뒤 검증 실패를 주입하는 도구.
    - fail_second: True이면 두 번째 계열까지 변경한 뒤 전체 취소를 검증.
    """
    storage = revision_storage
    first = original(storage)
    old_release, old_item = first.release.current, first.items[0].item.current
    second = ReleaseService(storage).register_release(ReleaseInput(family_id=2, version_raw="v1", version_order=1,
        batch_id=old_release["batch_id"], source_reference=old_release["source_reference"]))
    ReleaseService(storage).save_item_links(second.release.current["release_version_id"],
        (ItemLinkInput(item_id=old_item["item_id"], item_state=ItemState.APPLIED),))
    saver = DatabaseImportSaver(storage)
    a = import_request(storage, identity="first-family", sheet="Oxide노트", content="O2 새 내용")
    b = import_request(storage, identity="second-family", sheet="Poly노트", content="C3 새 내용")
    values = {**b.rows[0].values, "group_name": "POLY", "model_name": "C3", "family_name": "C3", "ui_type": "LEGACY"}
    cells = {**b.raw_data["cells"], "B1": "C3", "E1": "C3", "F1": "Poly", "G1": "구UI"}
    row = replace(b.rows[0], values=values, family_id=2,
                  source_reference={**b.rows[0].source_reference, "ui_type": {"sheet": "Poly노트", "cells": ["G1"]}})
    b = replace(b, rows=(row,), raw_data={"sheet": "Poly노트", "cells": cells})
    plans = saver.preview_release_import((a, b))
    reviews = tuple(ReplacementReview(fingerprint=p.preview.fingerprint,
        choices={(c.kind, c.key, c.field): "INCOMING" for c in p.preview.conflicts},
        link_choices={(c.release_key, c.item_key): "INCOMING" for c in p.preview.link_conflicts}) for p in plans)
    requests = tuple(replace(r, release_reviews=reviews) for r in (a, b))
    tables = ("release_version", "item", "import_batch", "release_version_item", "risk_rule")
    before = {table: all_rows(storage, table) for table in tables}
    original_replace, changed = FamilyReplacementService.replace_in, []

    def apply_then_fail(self, tx, request, review, **kwargs):
        """두 계열 모두 실제 변경한 뒤 실패를 주입한다.

        param
        - self, tx, request, review, kwargs: 실제 계열 교체 호출 값.

        return
        - 실패 주입 전 실제 교체 결과.

        raises
        - StorageValidationError: 두 번째 계열 변경 후 검증용 실패.
        """
        result = original_replace(self, tx, request, review, **kwargs)
        changed.append(request.family_id)
        if fail_second and len(changed) == 2:
            raise StorageValidationError("두 번째 계열 저장 실패")
        return result

    monkeypatch.setattr(FamilyReplacementService, "replace_in", apply_then_fail)
    if fail_second:
        with pytest.raises(StorageValidationError, match="두 번째 계열 저장 실패"):
            saver.save_many(requests)
        assert changed == [1, 2]
        assert {table: all_rows(storage, table) for table in tables} == before
        assert saver._saved_requests == set()
    else:
        receipts = saver.save_many(requests)
        assert changed == [1, 2] and all(receipt.refresh is not None for receipt in receipts)
        assert len(all_rows(storage, "release_version")) == 2
        assert {item["content"] for item in all_rows(storage, "item")} == {"O2 새 내용", "C3 새 내용"}
        saved_original = next(row for row in all_rows(storage, "import_batch") if row["batch_id"] == old_release["batch_id"])
        assert saved_original == next(row for row in before["import_batch"] if row["batch_id"] == old_release["batch_id"])
