"""실제 DB 조회와 가져오기 후 화면 무효화·모델별 N 재확인 연결을 검증한다."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from datetime import date, datetime

import pytest
from PySide6.QtWidgets import QApplication

from setron.features.excel_import.schemas import SaveReceipt
from setron.features.import_save.service import DatabaseImportSaver
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.revision import ReleaseRevisionService
from setron.features.release.schemas import ItemInput, ItemLinkInput, ReleaseInput
from setron.features.release.service import ReleaseService
from setron.features.risk.schemas import RiskStatus
from setron.features.targets.schemas import CandidateConfirmation
from setron.features.targets.service import TargetService
from setron.ui.app import create_window
from setron.ui.database_source import DatabaseDashboardSource, load_dashboard_source
from setron.ui.equipment_data import equipment_data
from setron.ui.query_state import AppliedQuery, QuerySelection, TargetN

from test_release_revision import observation, revision_storage


def connected(storage):
    """UI에 연결 가능한 모델과 실제 출처 확인을 가진 목표를 준비한다.

    param
    - storage: 격리한 실제 저장 트랜잭션.

    return
    - 현재 릴리즈 ID와 DB를 다시 읽는 조회 공급자.
    """
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
    detail = ReleaseService(storage).register_release(ReleaseInput(family_id=1, version_raw="v1", version_order=1))
    selected = detail.release.current["release_version_id"]
    observation(storage, versions=((1, 1, "v1", selected),))
    TargetService(storage).confirm(1, 1, (CandidateConfirmation(
        selected, "합성 자료 모델 적용 표시 확인", "합성 자료 순서 확인", source_text="검증용 원본"),))
    return selected, DatabaseDashboardSource(lambda: load_dashboard_source(storage))


def test_live_database_query_revalidates_n_proof_without_changing_choice(revision_storage):
    """DB 변경 후 기존 N을 자동 변경하지 않고 최신 근거가 없으면 판단 불가·재선택으로 표시한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    """
    selected, source = connected(revision_storage)
    query = AppliedQuery(QuerySelection(), 1, 0, "전체", (TargetN(1, 1, selected),))
    assert source.snapshot(query).summary.compliant == 1
    ReleaseRevisionService(revision_storage).update_release(selected, {"released_at": date(2026, 10, 2)})
    result = source.snapshot(query)
    assert result.query is query and query.targets[0].release_id == selected
    assert result.summary.compliant == 0 and result.summary.undetermined == 1
    assert "목표 N 재선택" in result.source_label
    assert result.target_values == ()


def test_live_database_query_uses_saved_item_risk_judgment(revision_storage):
    """다음 조회가 현재 Item 연결과 수동 위험 규칙을 사용해 위험 대수를 갱신하는지 확인한다.

    param
    - revision_storage: 독립적인 업무 저장소.
    """
    selected, source = connected(revision_storage)
    service = ReleaseRevisionService(revision_storage)
    item_id = service.register_item(selected, ItemInput(content="합성 안전 개선")).value.current["item_id"]
    service.save_item_links(selected, (ItemLinkInput(item_id=item_id, item_state=ItemState.NOT_APPLIED,
                                                  target_scope=TargetScope.ALL),))
    with revision_storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "SAFETY_REQUIRED", "family_id": 1, "item_id": item_id,
            "risk_reason": "검증용 위험", "target_reference": {}, "mapping_status": "VERIFIED"})
    query = AppliedQuery(QuerySelection(), 1, 0, "전체", (TargetN(1, 1, selected),))
    assert len(source.snapshot(query).risky_ids) == 1
    service.save_item_links(selected, (ItemLinkInput(item_id=item_id, item_state=ItemState.APPLIED,
                                                  target_scope=TargetScope.ALL),))
    assert source.snapshot(query).risky_ids == ()


@pytest.mark.parametrize(("mapping_status", "expected"), (
    ("VERIFIED", RiskStatus.RISK), ("UNRESOLVED", RiskStatus.UNKNOWN),
))
def test_live_database_risk_uses_displayed_model_scope(revision_storage, mapping_status, expected):
    """혼합 PM 설비의 다른 모델 위험을 선택 모델의 대수·셀·타일에 전파하지 않는지 확인한다.

    param
    - revision_storage: 실제 외래키를 사용하는 격리한 업무 DB.
    - mapping_status, expected: O2 전용 위험 규칙의 연결 상태와 해당 모델의 예상 판정.
    """
    storage = revision_storage
    with storage.transaction() as tx:
        tx.update("equipment_group", {"group_id": 1}, {"group_name": "M-O2"})
        tx.update("equipment_group", {"group_id": 3}, {"group_name": "M-O3", "category": "OXIDE"})
        tx.update("version_family", {"family_id": 2}, {"family_name": "O3"})
    service = ReleaseService(storage)
    first = service.register_release(ReleaseInput(family_id=1, version_raw="v1", version_order=1))
    second = service.register_release(ReleaseInput(family_id=2, version_raw="v1", version_order=1))
    first_id = first.release.current["release_version_id"]
    second_id = second.release.current["release_version_id"]
    observation(storage, versions=((1, 1, "v1", first_id), (2, 3, "v1", second_id)), pms=True)
    with storage.transaction() as tx:
        tx.insert("risk_rule", {"rule_type": "DIRECT_VERSION", "family_id": 1, "group_id": 1,
            "release_version_id": first_id, "risk_reason": "검증용 O2 모델 전용 위험",
            "target_reference": {}, "mapping_status": mapping_status})
    source = DatabaseDashboardSource(lambda: load_dashboard_source(storage))
    targets = (TargetN(1, 1, None), TargetN(3, 2, None))
    scoped = source.snapshot(AppliedQuery(QuerySelection(group_key="o3"), 1, 0, "O3", targets))
    assert scoped.equipment_ids and scoped.risky_ids == () and scoped.risk_unknown == 0
    detail, = equipment_data(scoped, source).details
    assert detail.selected.risk is RiskStatus.NO_RISK and detail.whole.risk is expected
    assert {pm.group_id for pm in scoped.selected_records[0].pms} == {3}
    complete = source.snapshot(AppliedQuery(QuerySelection(), 2, 0, "전체", targets))
    o3 = next(cell for cell in complete.groups if cell.group_keys == ("o3",))
    assert o3.equipment_ids == scoped.equipment_ids and o3.risky_ids == ()
    assert complete.risky_ids == (complete.equipment_ids if expected is RiskStatus.RISK else ())
    assert complete.risk_unknown == (1 if expected is RiskStatus.UNKNOWN else 0)


@pytest.fixture(scope="module")
def app():
    """화면 이벤트 검증에 사용할 QApplication을 공유한다.

    return
    - 현재 또는 새 QApplication.
    """
    return QApplication.instance() or QApplication([])


def test_app_captures_applied_targets_and_invalidates_results_after_save(app, revision_storage):
    """현재 적용 N을 저장 요청에 전달하고 성공 후 오래된 결과만 지우는지 확인한다.

    param
    - app: 화면 이벤트 환경.
    - revision_storage: 독립적인 업무 저장소.
    """
    selected, source = connected(revision_storage)
    saver = DatabaseImportSaver(revision_storage)
    # 이 테스트의 메모리 DB는 GUI 스레드 전용이다. 실제 워커 조회는 파일 DB 회귀에서 검증한다.
    window = create_window(source, import_saver=saver, version_loader=source.load_version_catalog,
                           item_loader=source.load_item_data)
    try:
        state = window.query_state
        window.query_panel.set_target_draft((TargetN(1, 1, selected),))
        window.query_panel.apply_draft()
        state.set_target_draft((TargetN(1, 1, None),))
        captured = window.release_refresh_context()
        assert captured.targets == {(1, 1): selected} and captured.use_saved_applicability
        importer = window.pages["import"]
        result = ReleaseRevisionService(revision_storage, captured).update_release(
            selected, {"released_at": date(2026, 10, 3)})
        importer.saved.emit(SaveReceipt(1, 1, datetime.now(), "검증 DB", refresh=result.refresh))
        assert window.pages["dashboard"].snapshot is None
        assert window.pages["heatmap"].snapshot is None
        assert state.applied.targets == (TargetN(1, 1, selected),)
        assert state.target_draft == (TargetN(1, 1, None),)
        assert "목표 N 재선택" in importer.result_label.text()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
