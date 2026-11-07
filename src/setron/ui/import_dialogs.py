"""가져오기 전용 진행·확인 창. Figma의 둥근 흰색 카드와 기존 Qt 컨트롤을 사용한다."""

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QMessageBox, QProgressBar, QScrollArea, QVBoxLayout,
)

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
        shadow.setColor(QColor(0, 0, 0, 40))
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


class ImportChoiceDialog(ImportDialog):
    def __init__(self, title, text, options, parent=None):
        super().__init__(title, parent)
        self.message = self.add_message(text)
        self.choices = QComboBox()
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
    """테마에 의존하지 않는 왕복 없는 대기 표시. 완료율을 추정하지 않는다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(1400)
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QPainterPath()
        track.addRoundedRect(QRectF(self.rect()), 3, 3)
        painter.fillPath(track, QColor("#e8edfc"))
        painter.setClipPath(track)
        segment = max(32.0, self.width() * 0.28)
        start = self.phase * (self.width() + segment) - segment
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PRIMARY))
        painter.drawRoundedRect(QRectF(start, 0, segment, self.height()), 3, 3)


class ImportProgressDialog(ImportDialog):
    """비동기 작업 중 보이는 창. 닫기를 실제 작업 취소인 것처럼 표시하지 않는다."""

    def __init__(self, parent=None):
        super().__init__("처리 중", parent)
        self.message = self.add_message("")
        self.progress = ImportBusyBar()
        self.body.addWidget(self.progress)
        self.hint = label("완료되면 자동으로 닫힙니다.", 12, color=SECONDARY, wrap=True)
        self.body.addWidget(self.hint)
        self.operation = None

    def set_operation(self, operation, title="", text="", *, visible=True):
        if operation is None:
            self.operation = None
            self.progress.setRange(0, 1)
            self.hide()
            return
        self.operation = operation
        self.heading.setText(title)
        self.setWindowTitle(title)
        self.message.setText(text)
        self.message.scroll_area.setFixedHeight(max(38, min(90, self.message.heightForWidth(self.dialog_width - 104) + 8)))
        self.hint.setText("완료되면 자동으로 닫힙니다. 잠시만 기다려 주세요.")
        self.progress.setRange(0, 0)
        if visible and not self.isVisible():
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
