"""자료 설명·관측 연결·조회 상태를 실제 DB 없이 검증한다."""

from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from setron.features.item.schemas import Item
from setron.ui.app import create_window
from setron.ui.item_data import ItemManagerData
from setron.ui.items import ItemsPage
from setron.ui.source_notice import source_description
from setron.ui.version_data import ItemEntry, VersionCatalog
from setron.ui.versions import VersionsPage


@pytest.fixture(scope="module")
def app():
    """기존 Qt 앱 또는 새 앱을 제공한다.

    return
    - QApplication.
    """
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("description", ["검토용 메모리 자료", "합성 예시 · DB 미연결", "<b>원문 설명</b>"])
def test_custom_description_without_fixed_demo_claim(app, description):
    """상단 안내가 외부 자료를 예시/DB 연결로 단정하지 않는지 확인한다.

    param
    - app: Qt 앱.
    - description: 외부에서 전달한 출처 설명.
    """
    catalog = VersionCatalog((), (), source_label=description)
    window = create_window(version_loader=lambda: catalog)
    try:
        window.navigate("versions")
        page = window.pages["versions"]
        assert "본문 참조" in window.notice.text()
        assert "예시" not in window.notice.text() and "DB" not in window.notice.text()
        assert page.notice.text() == source_description(description)
        assert page.notice.textFormat() is Qt.TextFormat.PlainText
        window.navigate("items")
        assert window.pages["items"].notice.text() == source_description(description)
        assert not window.pages["items"].data.observations_connected
    finally:
        window.close()


def test_default_demo_still_identified(app):
    """기본 합성 자료의 예시/DB 미연결 설명은 보존한다.

    param
    - app: Qt 앱.
    """
    window = create_window()
    try:
        window.navigate("versions")
        text = window.pages["versions"].notice.text()
        assert "합성" in text and "DB 미연결" in text
    finally:
        window.close()


@pytest.mark.parametrize("description", ["", "   ", None])
def test_missing_description_does_not_imply_disconnected(description):
    """출처 설명 누락을 연결 실패로 추정하지 않는다.

    param
    - description: 미제공 설명.
    """
    assert source_description(description) == "자료 출처: 설명 미제공"
    assert "미연결" not in VersionCatalog((), ()).source_label


@pytest.mark.parametrize("connected", [False, True])
def test_observation_state_is_explicit_not_label(app, connected):
    """설명 기본값과 정상 0건·관측 미연결이 모순되지 않는지 확인한다.

    param
    - app: Qt 앱.
    - connected: 자료 계약에 명시된 관측 연결 상태.
    """
    catalog = VersionCatalog((), (), items=(ItemEntry(Item(1, 1, "내용")),))
    data = ItemManagerData(catalog, base_date=date(2026, 11, 2) if connected else None,
                           observations_connected=connected)
    page = ItemsPage(lambda: data)
    try:
        assert page.open_item_id(1)
        assert page.title.text() == "내용" and not hasattr(page, "observation_note")
        text = page.no_data_reason.text()
        assert not page.no_data_notice.isHidden() and page.summary.isHidden()
        if connected:
            assert "연결된 릴리즈가 없어" in text and "설비 관측 자료가 없어" not in text
        else:
            assert "설비 관측 자료가 없어" in text
    finally:
        page.close()


def test_success_empty_and_failure_do_not_share_count(app):
    """정상 빈 Item 자료를 미연결로 표시하거나 실패를 정상 0건으로 표시하지 않는다.

    param
    - app: Qt 앱.
    """
    page = ItemsPage(lambda: ItemManagerData(VersionCatalog((), ())))
    try:
        assert "0건" in page.count.text()
        assert "미연결" not in page.empty.text()
        page.loader = lambda: None
        page.reload()
        assert "미확인" in page.count.text() and "0건" not in page.count.text()
        assert "실패" in page.status.text()
        assert "자료 출처:" not in page.notice.text()
    finally:
        page.close()


def test_release_failure_clears_source_and_count(app):
    """버전 조회 실패 시 이전 출처 설명과 정상 건수를 남기지 않는다.

    param
    - app: Qt 앱.
    """
    page = VersionsPage(lambda: VersionCatalog((), (), source_label="이전 자료"))
    try:
        page.loader = lambda: None
        page.reload()
        assert "이전 자료" not in page.notice.text()
        assert "미확인" in page.count.text() and "0건" not in page.count.text()
    finally:
        page.close()
