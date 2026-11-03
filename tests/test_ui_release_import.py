"""실제 가져오기 화면의 전체 노트 확인·비동기 충돌 검토·명시 선택 전달 검증."""

from dataclasses import replace
from datetime import datetime
from threading import Event
from time import monotonic
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest

from setron.features.ai.schemas import ExtractKind
from setron.features.excel_import.schemas import SaveReceipt
from setron.features.import_save.service import DatabaseImportSaver, ReleaseImportPlan
from setron.features.item.schemas import ItemState, TargetScope
from setron.features.release.refresh_schemas import RefreshContext
from setron.features.release.replacement_schemas import (
    LinkConflict, ReplacementItem, ReplacementLink, ReplacementPreview, ReplacementRelease,
    ReplacementRequest, ReplacementReview,
)
from setron.features.release.schemas import ItemInput, ReleaseInput
from setron.ui.import_page import ImportPage
from test_import_validation import draft, row, reference, family
from test_ui_import import app


class ReviewingReleaseSaver(DatabaseImportSaver):
    """릴리즈 저장 화면의 실제 계약을 관찰하는 DB 미연결 공급자."""

    def __init__(self):
        """호출 관찰 목록과 취소 검증용 대기 신호를 준비한다."""
        self.previews, self.saved = [], []
        self.release = None

    def load_reference(self, drafts):
        """계열이 확인된 합성 검토 기준을 반환한다.

        param
        - drafts: 검사할 초안.

        return
        - 합성 계열 기준 자료.
        """
        return reference(family())

    def preview_release_import(self, request, reviews=()):
        """명시 선택 전에는 포함 관계 충돌을 반환한다.

        param
        - request, reviews: 확인한 전체 노트와 현재 선택.

        return
        - 충돌 선택을 반영한 계열별 검토 결과.
        """
        self.previews.append((request, reviews))
        if self.release is not None:
            assert self.release.wait(5)
        current = ReplacementLink(item_key="item", item_state=ItemState.NOT_APPLIED,
                                  target_scope=TargetScope.LISTED, target_group_ids=(1,))
        incoming = ReplacementLink(item_key="item", item_state=ItemState.APPLIED)
        value = ReplacementRequest(family_id=1,
            releases=(ReplacementRelease(key="release", value=ReleaseInput(family_id=1, version_raw="v1"), links=(incoming,)),),
            items=(ReplacementItem(key="item", value=ItemInput(item_no=7, content="내용")),), raw_data={},
            complete_note_confirmed=True, filename_confirmed=True)
        selected = bool(reviews and reviews[0].link_choices)
        preview = ReplacementPreview("fingerprint", {"release": 1}, {"item": 1}, (), (),
            link_conflicts=() if selected else (LinkConflict("release", "item", current, incoming),))
        return (ReleaseImportPlan(value, preview, "O2", {1: "v1"}, {1: "Item 7 · 내용"}, model_labels={1: "M-O2"}),)

    def save(self, request):
        """선택한 저장 요청을 기록한다.

        param
        - request: 파일명·전체 노트·매칭·수정값 확인이 끝난 요청.

        return
        - 합성 저장 성공 결과.
        """
        self.saved.append(request)
        return SaveReceipt(1, len(request.rows), datetime.now(), self.target_label)


def wait(page):
    """Qt 워커의 현재 작업이 끝날 때까지 이벤트를 처리한다.

    param
    - page: 검증할 가져오기 화면.
    """
    until = monotonic() + 5
    while page.busy and monotonic() < until:
        QTest.qWait(10)
    assert not page.busy


@pytest.fixture
def page(app):
    """전체 노트 확인과 검토 선택을 제어할 수 있는 가져오기 화면을 준비한다.

    param
    - app: Qt 앱 fixture.

    return
    - 독립적인 릴리즈 가져오기 화면.
    """
    saver = ReviewingReleaseSaver()
    result = ImportPage(saver=saver)
    result.confirms = []
    result.confirm = lambda text: result.confirms.append(text) or True
    result.draft = draft(row(content="내용", item_no="7"), kind=ExtractKind.RELEASE_NOTE, sheet="노트")
    result._changed(initial=True)
    wait(result)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_release_save_requires_filename_and_whole_note_confirmation(page):
    """전체 누적 노트·제외 안내를 확인하지 않으면 DB 교체 검토도 시작하지 않는지 검증한다.

    param
    - page: 검증용 릴리즈 화면.
    """
    page.confirm = lambda text: page.confirms.append(text) or False
    assert not page.save()
    assert page.saver.previews == [] and page.saver.saved == []
    assert "전체 누적 릴리즈 노트" in page.confirms[0] and page.draft.workbook_name in page.confirms[0]


def test_manual_link_choice_and_captured_targets_reach_actual_saver(page):
    """명시한 현재값 선택과 GUI에서 복사한 N 조건을 저장 경계까지 전달하는지 검증한다.

    param
    - page: 검증용 릴리즈 화면.
    """
    targets = {(1, 1): 1}
    page.refresh_context_provider = lambda: RefreshContext(targets=targets, use_saved_applicability=True)
    observed = []

    def choose(parent, plans, reviews, **kwargs):
        """사용자가 현재 포함 상태·모델 범위를 고르는 흐름을 재현한다.

        param
        - parent, plans, reviews: 화면과 이번 단계 검토 자료.
        - kwargs: 위험 확인 상태.

        return
        - 선택 결과와 다음 검토 필요 여부.
        """
        observed.append(plans)
        if not reviews:
            targets[(1, 1)] = 2
            return (ReplacementReview(fingerprint="fingerprint", link_choices={("release", "item"): "CURRENT"}),), True
        return reviews, False

    page.release_review_chooser = choose
    assert page.save()
    wait(page)
    assert page.state == "saved" and len(observed) == 2
    saved, = page.saver.saved
    assert saved.release_note_confirmed and saved.filename_confirmed
    assert saved.release_reviews[0].link_choices[("release", "item")] == "CURRENT"
    assert saved.refresh_context.targets == {(1, 1): 1}
    assert saved.refresh_context.use_saved_applicability


def test_cancel_release_conflict_keeps_draft_and_does_not_save(page):
    """충돌 선택 취소 시 초안과 DB를 보존하는지 검증한다.

    param
    - page: 검증용 릴리즈 화면.
    """
    initial = page.draft
    page.release_review_chooser = lambda *args, **kwargs: (None, False)
    assert page.save()
    wait(page)
    assert page.state == "idle" and page.draft is initial and page.saver.saved == []


def test_reset_discards_late_replacement_preview(page):
    """시트를 다시 읽은 뒤 도착한 이전 교체 검토가 새 초안을 저장하지 않는지 검증한다.

    param
    - page: 검증용 릴리즈 화면.
    """
    page.saver.release = Event()
    page.release_review_chooser = lambda *args, **kwargs: pytest.fail("이전 시트의 검토를 수락했습니다.")
    assert page.save()
    page._reset_draft()
    page.saver.release.set()
    QTest.qWait(60)
    assert page.draft is None and page.saver.saved == [] and page.state == "idle"
