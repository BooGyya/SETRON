"""실제 DB 없이 느린 공급자와 화면 스레드의 읽기 경계를 검증한다."""

from threading import Event, get_ident
from time import monotonic

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.item.schemas import Item
from setron.features.version.schemas import ReleaseVersion
from setron.ui.item_data import ItemManagerData
from setron.ui.items import ItemsPage
from setron.ui.read_worker import BackgroundRead
from setron.ui.version_data import ItemEntry, ModelFamily, ReleaseEntry, VersionCatalog
from setron.ui.versions import VersionsPage


def wait_until(predicate):
    """UI 이벤트를 처리하며 조건을 제한 시간 내 확인한다.

    param
    - predicate: 기다릴 조건 함수.
    """
    end = monotonic() + 5
    while not predicate() and monotonic() < end:
        QTest.qWait(10)
    assert predicate()


class SlowRead:
    """테스트가 응답 시점을 제어하는 가짜 공급자.

    ivar
    - value: 성공 결과 또는 발생시킬 예외.
    - started: 실행 시작 표시.
    - release: 반환 허용 표시.
    - thread: 실제 실행 스레드 ID.
    """

    def __init__(self, value):
        """지연 결과를 보관한다.

        param
        - value: 결과 또는 예외.
        """
        self.value = value
        self.started, self.release = Event(), Event()
        self.thread = None
        self.calls = 0

    def __call__(self):
        """신호를 기다린 뒤 결과를 반환한다.

        return
        - 합성 자료.

        raises
        - Exception: 지정된 실패 또는 테스트 제한 시간 초과.
        """
        self.calls += 1
        self.thread = get_ident()
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("테스트 공급자 제한 시간")
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.fixture(scope="module")
def app():
    """공유 Qt 앱을 제공한다.

    return
    - QApplication.
    """
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=[VersionsPage, ItemsPage])
def context(app, request):
    """두 화면에 동일한 지연 시나리오를 제공한다.

    param
    - app: Qt 앱.
    - request: 화면 종류 매개변수.

    return
    - 화면 생성 함수, 읽기 결과 접근 함수, 합성 자료.
    """
    catalog = VersionCatalog((ModelFamily(1, "모델", 2, "SW"),),
                             (ReleaseEntry(ReleaseVersion(3, 2, "v1"), (1,)),),
                             (ItemEntry(Item(4, 1, "내용")),), source_label="지연 테스트 자료")
    kind = request.param
    value = catalog if kind is VersionsPage else ItemManagerData(catalog)
    pages, loaders = [], []

    def create(loader):
        """화면을 생성하고 테스트 종료 시 정리 대상으로 등록한다.

        param
        - loader: 가짜 공급자.

        return
        - 조회 화면.
        """
        loaders.append(loader)
        page = kind(BackgroundRead(loader))
        pages.append(page)
        page.show()
        return page

    yield create, lambda page: page.catalog if kind is VersionsPage else page.data, value, loaders
    for loader in loaders:
        loader.release.set()
    for page in pages:
        page.close()
    QThreadPool.globalInstance().waitForDone(6000)
    QTest.qWait(10)


def test_loading_responsive_and_duplicate_blocked(context):
    """느린 조회 중에도 UI 이벤트가 실행되고 중복 공급자 호출은 차단되는지 확인한다.

    param
    - context: 두 조회 화면의 테스트 환경.
    """
    create, result, value, _ = context
    slow = SlowRead(value)
    page = create(slow)
    wait_until(slow.started.is_set)
    assert slow.thread != get_ident()
    assert page.read_request.busy and not page.refresh.isEnabled()
    assert "불러오는 중" in page.status.text() and result(page) is None
    tick = []
    QTimer.singleShot(0, lambda: tick.append(get_ident()))
    wait_until(lambda: bool(tick))
    page.reload()
    assert slow.calls == 1
    completed = []
    page.read_request.completed.connect(lambda *_: completed.append(get_ident()))
    slow.release.set()
    wait_until(lambda: not page.read_request.busy)
    assert result(page) is value and page.refresh.isEnabled()
    assert completed == [get_ident()]


@pytest.mark.parametrize("bad", [RuntimeError("공급자 실패"), object()])
def test_failure_and_retry(context, bad):
    """공급자 예외와 잘못된 반환값 이후 재시도가 가능한지 확인한다.

    param
    - context: 화면 환경.
    - bad: 실패할 응답.
    """
    create, result, value, loaders = context
    slow = SlowRead(bad)
    page = create(slow)
    slow.release.set()
    wait_until(lambda: not page.read_request.busy)
    assert result(page) is None and "실패" in page.status.text()
    assert page.refresh.isEnabled()
    retry = SlowRead(value)
    loaders.append(retry)
    page.loader = BackgroundRead(retry)
    page.reload()
    assert "불러오는 중" in page.status.text()
    retry.release.set()
    wait_until(lambda: not page.read_request.busy)
    assert result(page) is value


@pytest.mark.parametrize("late_error", [False, True])
def test_leave_retry_and_late_response(context, late_error):
    """이탈 전 응답이 새 조회를 덮어쓰지 않는지 성공·실패 모두 확인한다.

    param
    - context: 화면 환경.
    - late_error: 이전 응답을 실패로 만들지 여부.
    """
    create, result, value, loaders = context
    empty_catalog = VersionCatalog((), ())
    old_value = empty_catalog if isinstance(value, VersionCatalog) else ItemManagerData(empty_catalog)
    first = SlowRead(RuntimeError("이전 실패") if late_error else old_value)
    page = create(first)
    wait_until(first.started.is_set)
    page.hide()
    assert not page.read_request.busy and "취소" in page.status.text()
    page.show()
    second = SlowRead(value)
    loaders.append(second)
    page.loader = BackgroundRead(second)
    page.reload()
    second.release.set()
    wait_until(lambda: not page.read_request.busy)
    before = page.status.text()
    first.release.set()
    assert QThreadPool.globalInstance().waitForDone(2000)
    QTest.qWait(20)
    assert result(page) is value and page.status.text() == before


def test_deleted_page_ignores_completion(app):
    """삭제된 화면으로 완료 콜백을 전달하지 않는지 확인한다.

    param
    - app: Qt 앱.
    """
    slow = SlowRead(VersionCatalog((), ()))
    page = VersionsPage(BackgroundRead(slow))
    completed = []
    page.read_request.completed.connect(lambda *_: completed.append(True))
    wait_until(slow.started.is_set)
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    slow.release.set()
    assert QThreadPool.globalInstance().waitForDone(2000)
    QTest.qWait(20)
    assert completed == []


def test_reload_clears_previous_detail_and_accepts_empty(context):
    """재조회 즉시 이전 상세를 비우고 정상 0건을 실패와 구분한다.

    param
    - context: 화면 환경.
    """
    create, result, value, loaders = context
    first = SlowRead(value)
    page = create(first)
    first.release.set()
    wait_until(lambda: not page.read_request.busy)
    page.open_row(0)
    assert page.mode == "detail"
    empty_catalog = VersionCatalog((), ())
    empty = empty_catalog if isinstance(page, VersionsPage) else ItemManagerData(empty_catalog)
    second = SlowRead(empty)
    loaders.append(second)
    page.loader = BackgroundRead(second)
    page.reload()
    assert page.mode == "list" and page.selected_id is None
    assert result(page) is None and page.list_model.rowCount() == 0
    second.release.set()
    wait_until(lambda: not page.read_request.busy)
    assert result(page) is empty and "실패" not in page.status.text()


def test_app_inherits_background_boundary_without_demo_observations(app):
    """앱의 파생 Item 공급자도 워커를 사용하며 합성 관측을 붙이지 않는지 확인한다.

    param
    - app: Qt 앱.
    """
    from setron.ui.app import create_window

    slow = SlowRead(VersionCatalog((), ()))
    window = create_window(version_loader=BackgroundRead(slow))
    try:
        versions, items = window.pages["versions"], window.pages["items"]
        assert isinstance(items.loader, BackgroundRead)
        # 앱 등록 과정에서 비활성 페이지는 숨겨진다. 취소된 요청은 명시적으로 재조회한다.
        versions.reload()
        items.reload()
        assert versions.read_request.busy and items.read_request.busy
        slow.release.set()
        wait_until(lambda: not versions.read_request.busy and not items.read_request.busy)
        assert items.data is not None and not items.data.observations_connected
        assert items.data.records == ()
    finally:
        slow.release.set()
        window.close()
        QThreadPool.globalInstance().waitForDone(2000)
