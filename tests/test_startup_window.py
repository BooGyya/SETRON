"""시작 화면의 글자·원본 로고와 실제 완료 단계 진행률을 검증한다."""

import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSize, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from setron.ui.appearance import manager
from setron.ui.appearance_data import Appearance
from setron.ui.main_window import BrandLogo
from setron.ui.startup_window import StartupWindow
from setron.ui.theme import ASSETS, install_theme


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    return result


@pytest.fixture
def startup(app):
    controller = manager()
    original = controller.appearance
    window = StartupWindow()
    window.show()
    app.processEvents()
    yield window
    if isValid(window):
        window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    controller.apply(original)
    app.processEvents()


def check_text_and_layout(window):
    message, percentage, progress, logo = window.message, window.percentage, window.progress, window.logo
    for text in (message, percentage):
        assert not text.wordWrap() and text.textFormat() == Qt.TextFormat.PlainText
        assert text.contentsRect().width() >= text.fontMetrics().horizontalAdvance(text.text())
        assert text.contentsRect().height() >= text.fontMetrics().height() + 4
    message_pos, percentage_pos = message.mapTo(window.card, QPoint()), percentage.mapTo(window.card, QPoint())
    assert message_pos.x() + message.width() + 12 <= percentage_pos.x()
    assert logo.mapTo(window.card, QPoint()).y() + logo.height() <= message_pos.y()
    assert max(message_pos.y() + message.height(), percentage_pos.y() + percentage.height()) < progress.mapTo(window.card, QPoint()).y()
    assert progress.width() > 0 and progress.height() == 8
    card_rect = window.card.rect().translated(window.card.mapTo(window.centralWidget(), QPoint()))
    assert window.centralWidget().rect().contains(card_rect)
    assert not logo.pixmap().isNull()
    assert logo.pixmap().deviceIndependentSize().toSize() == logo.size()


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("font_size", ["small", "normal", "large"])
@pytest.mark.parametrize("size", [(960, 600), (1440, 960)])
def test_all_stage_messages_fit_after_live_theme_font_and_window_changes(startup, app, mode, font_size, size):
    manager().apply(Appearance(mode=mode, font_size=font_size))
    startup.resize(*size)
    app.processEvents()
    check_text_and_layout(startup)
    for name in ("settings", "source", "window", "query"):
        assert startup.complete_step(name)
        app.processEvents()
        check_text_and_layout(startup)
    assert startup.logo.property("brandMode") == mode
    assert startup.logo.size() == QSize(260, 80)


def test_progress_only_counts_completed_steps_and_does_not_advance_with_time(startup, app):
    assert (startup.progress.minimum(), startup.progress.maximum(), startup.progress.value()) == (0, 4, 0)
    assert startup.percentage.text() == "0% · 0/4"
    with pytest.raises(ValueError):
        startup.complete_step("source")
    assert startup.progress.value() == 0
    assert startup.complete_step("settings")
    assert not startup.complete_step("settings")
    assert startup.progress.value() == 1 and startup.percentage.text() == "25% · 1/4"
    QTest.qWait(80)
    assert startup.progress.value() == 1 and startup.completed_steps == 1
    with pytest.raises(ValueError):
        startup.complete_step("query")
    assert startup.completed_steps == 1
    for count, name in enumerate(("source", "window", "query"), 2):
        assert startup.complete_step(name)
        assert startup.progress.value() == count
        assert startup.percentage.text() == f"{count * 25}% · {count}/4"
        assert startup.progress.accessibleDescription() == f"초기화 {count}/4 단계 완료"
    assert startup.message.text() == "실행 준비 완료"
    assert not startup.complete_step("query")


def test_closing_cancels_pending_read_once_and_rejects_more_progress(startup, app):
    startup.complete_step("settings")
    startup.request.busy = True
    generation = startup.request.generation
    closed = QSignalSpy(startup.closed)
    assert startup.close()
    assert not startup.accepting and not startup.request.busy
    assert startup.request.generation == generation + 1 and closed.count() == 1
    assert not startup.complete_step("source") and startup.progress.value() == 1
    startup.close()
    assert closed.count() == 1
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(startup)


def test_startup_uses_original_brand_asset_and_default_sidebar_logo_size_is_unchanged(startup, app):
    default = BrandLogo()
    try:
        assert default.size() == QSize(128, 36)
        assert startup.logo.size() == QSize(260, 80)
        assert (ASSETS / "setron-logo.png").is_file()
        for logo in (default, startup.logo):
            assert logo.property("brandAsset") == logo.property("displayedBrandAsset") == "setron-logo.png"
            assert logo.property("assetFile") is None
            assert not logo._source.isNull() and not logo.pixmap().isNull()
    finally:
        default.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("scale", ["1.25", "1.5", "2"])
def test_large_dark_startup_keeps_text_and_logo_at_fractional_and_high_dpr(scale):
    code = """
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from setron.ui.appearance import manager
from setron.ui.appearance_data import Appearance
from setron.ui.startup_window import StartupWindow
from setron.ui.theme import install_theme
app = QApplication([])
install_theme(app)
window = StartupWindow()
window.resize(960, 600)
window.show()
manager().apply(Appearance(mode='dark', font_size='large'))
for step in ('settings', 'source', 'window', 'query'):
    window.complete_step(step)
    app.processEvents()
    for text in (window.message, window.percentage):
        assert text.contentsRect().width() >= text.fontMetrics().horizontalAdvance(text.text())
        assert text.contentsRect().height() >= text.fontMetrics().height() + 4
    assert not window.logo.pixmap().isNull()
    assert window.logo.pixmap().deviceIndependentSize().toSize() == window.logo.size()
    assert abs(window.logo.pixmap().devicePixelRatio() - window.devicePixelRatioF()) < 0.01
assert window.devicePixelRatioF() > 1
assert window.centralWidget().rect().contains(window.card.rect().translated(window.card.mapTo(window.centralWidget(), QPoint())))
window.close()
app.processEvents()
"""
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale)
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=environment, capture_output=True, text=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    assert result.returncode == 0, result.stdout + result.stderr
