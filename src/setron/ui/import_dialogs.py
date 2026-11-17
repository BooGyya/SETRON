"""가져오기 전용 진행·확인 창. Figma의 둥근 흰색 카드와 기존 Qt 컨트롤을 사용한다."""

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QMessageBox, QProgressBar, QScrollArea, QVBoxLayout,
)

from setron.ui.appearance import manager, readable_color, refresh_widget_font, ui_color
from setron.ui.appearance_data import Appearance
from setron.ui.combo_box import ComboBox
from setron.ui.components import button, label
from setron.ui.import_widgets import import_combo_style
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY


class ImportDialog(QDialog):
    """16px 라운드·28/24px 여백. 긴 근거는 스크롤하고 취소를 기본 선택으로 둔다."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        available = self.screen().availableGeometry()
        self.dialog_width = min(600, max(320, available.width() - 48))
        self.setFixedWidth(self.dialog_width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        self.surface = QFrame()
        self.surface.setObjectName("importDialogSurface")
        self.surface.setStyleSheet(
            f"QFrame#importDialogSurface {{background:white;border:1px solid {LINE};border-radius:16px;}}")
        shadow = QGraphicsDropShadowEffect(self.surface)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(ui_color(0, 0, 0, 40))
        self.surface.setGraphicsEffect(shadow)
        outer.addWidget(self.surface)
        self.body = QVBoxLayout(self.surface)
        self.body.setContentsMargins(28, 24, 28, 24)
        self.body.setSpacing(14)
        self.heading = label(title, 18, medium=True, color=INK, wrap=True)
        self.body.addWidget(self.heading)
        self.footer = QHBoxLayout()
        self.footer.setSpacing(8)
        self.footer.addStretch()

    def add_message(self, text):
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget {background:transparent;}")
        message = label(text, 13, color=SECONDARY, wrap=True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        width = self.dialog_width - 104
        height = message.heightForWidth(width)
        limit = max(100, min(360, self.screen().availableGeometry().height() - 310))
        scroll.setFixedHeight(max(38, min(height + 8, limit)))
        scroll.setWidget(message)
        self.body.addWidget(scroll)
        message.scroll_area = scroll
        return message

    def add_action(self, text, callback, *, primary=False, default=False):
        action = button(text, primary=primary)
        action.setMinimumSize(96, 40)
        action.setAutoDefault(False)
        action.setDefault(default)
        action.clicked.connect(callback)
        self.footer.addWidget(action)
        return action

    def finish_layout(self):
        self.body.addLayout(self.footer)


class ImportConfirmDialog(ImportDialog):
    def __init__(self, text, parent=None, *, title="가져오기 저장 확인", action_text="저장"):
        super().__init__(title, parent)
        self.message = self.add_message(text)
        self.cancel_button = self.add_action("취소", self.reject, default=True)
        self.save_button = self.add_action(action_text, self.accept, primary=True)
        self.finish_layout()


def confirm_numbered_save(parent, path):
    dialog = ImportConfirmDialog(
        f"같은 이름의 파일이 있습니다.\n{path.name}\n\n계속 저장할까요?",
        parent, title="파일 덮어쓰기 확인", action_text="확인",
    )
    try:
        return dialog.exec() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


class ImportChoiceDialog(ImportDialog):
    def __init__(self, title, text, options, parent=None):
        super().__init__(title, parent)
        self.message = self.add_message(text)
        self.choices = ComboBox()
        self.choices.setProperty("queryField", True)
        self.choices.setAccessibleName(title + " 선택")
        self.choices.setStyleSheet(import_combo_style())
        self.choices.setFixedHeight(42)
        self.choices.addItems(options)
        for index, value in enumerate(options):
            self.choices.setItemData(index, value, Qt.ItemDataRole.ToolTipRole)
        self.body.addWidget(self.choices)
        self.cancel_button = self.add_action("취소", self.reject, default=True)
        self.confirm_button = self.add_action("선택 완료", self.accept, primary=True)
        self.confirm_button.setEnabled(bool(options))
        self.finish_layout()


def choose_import_option(parent, title, text, options):
    dialog = ImportChoiceDialog(title, text, options, parent)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    value = dialog.choices.currentText()
    dialog.deleteLater()
    return value, accepted


def import_question(parent, title, text, buttons, default):
    """위험 보존/재연결의 기존 Yes·No·Cancel 의미를 유지하며 한글 버튼을 표시한다."""
    dialog = ImportDialog(title, parent)
    dialog.add_message(text)
    result = QMessageBox.StandardButton.Cancel
    labels = ((QMessageBox.StandardButton.Cancel, "취소"), (QMessageBox.StandardButton.No, "미확인으로 유지"),
              (QMessageBox.StandardButton.Ok, "계속"), (QMessageBox.StandardButton.Yes, "다시 연결"))
    def choose(value):
        nonlocal result
        result = value
        dialog.accept()
    for value, text in labels:
        if buttons & value:
            dialog.add_action(text, lambda checked=False, v=value: choose(v),
                              primary=value in (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.Ok),
                              default=value == default)
    dialog.finish_layout()
    dialog.exec()
    dialog.deleteLater()
    return result


class ImportBusyBar(QProgressBar):
    """진행률을 제공하지 않는 작업에 쓰는 왕복 표시."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self._colors = None
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(2200)
        self.animation.setLoopCount(-1)
        self.animation.setEasingCurve(QEasingCurve.Type.Linear)
        self.animation.valueChanged.connect(self._advance)
        self.setTextVisible(False)
        self.setFixedHeight(6)
        self.setAccessibleName("작업 진행 중 · 진행률 미제공")
        self.setRange(0, 0)

    def _advance(self, value):
        self.phase = float(value)
        self.update()  # 6px 막대만 다시 그리며 레이아웃 크기는 바꾸지 않는다.

    def _sync_animation(self):
        app = QApplication.instance()
        reduced = bool(app and app.property("reducedMotion"))
        running = self.isVisible() and self.minimum() == self.maximum() == 0 and not reduced
        if running and self.animation.state() != QAbstractAnimation.State.Running:
            self.phase = 0.0
            self.animation.start()
        elif not running:
            self.animation.stop()
            self.phase = 0.5
            self.update()

    def setRange(self, minimum, maximum):
        super().setRange(minimum, maximum)
        self._sync_animation()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)

    def keep_current_colors(self):
        self._colors = self._theme_colors()
        self.update()

    @staticmethod
    def _theme_colors():
        track = ui_color("#e8edfc")
        fill = QColor(readable_color(ui_color(PRIMARY).name(), track.name(), 3))
        return track, fill

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QPainterPath()
        track.addRoundedRect(QRectF(self.rect()), 3, 3)
        track_color, fill = self._colors or self._theme_colors()
        painter.fillPath(track, track_color)
        painter.setClipPath(track)
        segment = min(self.width(), max(32.0, self.width() * 0.28))
        moving = self.animation.state() == QAbstractAnimation.State.Running
        position = 1 - abs(2 * self.phase - 1) if moving else 0.5
        start = position * (self.width() - segment)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(start, 0, segment, self.height()), 3, 3)


class ImportProgressDialog(ImportDialog):
    """비동기 작업 중 보이는 창. 닫기를 실제 작업 취소인 것처럼 표시하지 않는다."""

    def __init__(self, parent=None):
        super().__init__("처리 중", parent)
        self.message = self.add_message("")
        self.progress = ImportBusyBar()
        self.body.addWidget(self.progress)
        self.hint = label("", 12, color=SECONDARY, wrap=True)
        self.hint.hide()
        self.body.addWidget(self.hint)
        self.operation = None

    def _keep_current_theme(self):
        """진행 중 전역 테마가 바뀌어도 같은 팝업 색과 글자 크기를 유지한다."""
        controller = manager()
        colors = controller.tokens if controller else Appearance().colors()
        font_scale = controller.appearance.font_scale if controller else 1.0
        background = colors["surface"]
        self.setProperty("themePreview", True)
        self.setStyleSheet("QDialog {background:transparent;}")
        self.surface.setProperty("themePreview", True)
        self.surface.setStyleSheet(
            f"QFrame#importDialogSurface {{background:{background};border:1px solid {colors['line']};border-radius:16px;}}")
        for label_, preferred in ((self.heading, colors["ink"]), (self.message, colors["muted"]), (self.hint, colors["muted"])):
            label_.setProperty("themePreview", True)
            label_.setProperty("setronPreviewFontScale", font_scale)
            label_.setStyleSheet("color:" + readable_color(preferred, background) + ";")
            refresh_widget_font(label_)
        self.progress.keep_current_colors()

    def set_operation(self, operation, title="", text="", *, visible=True):
        if operation is None:
            self.operation = None
            self.progress.setRange(0, 1)
            self.hide()
            return
        if self.operation is None or (visible and not self.isVisible()):
            self._keep_current_theme()
        self.operation = operation
        self.heading.setText(title)
        self.setWindowTitle(title)
        self.message.setText(text)
        self.message.scroll_area.setFixedHeight(max(38, min(90, self.message.heightForWidth(self.dialog_width - 104) + 8)))
        self.progress.setRange(0, 0)
        if not visible:
            self.hide()
        elif not self.isVisible():
            self.adjustSize()
            self.open()

    def reject(self):
        if self.operation is None:
            super().reject()

    def closeEvent(self, event):
        if self.operation is not None:
            event.ignore()
        else:
            super().closeEvent(event)
