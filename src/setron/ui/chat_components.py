"""VER5 AI 응답의 시각 부품. 초기 화면과 분석 엔진은 의존하지 않는다."""
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QVBoxLayout, QLayout, QWidget, QProxyStyle, QStyleFactory, QCheckBox, QRadioButton,
    QGridLayout, QSizePolicy,
)

from setron.ui.appearance import ui_color
from setron.ui.components import button, label
from setron.ui.theme import SECONDARY, icon, svg_size


def asset_label(filename, *, slot=None):
    """원본 SVG 크기는 유지하고, 배경 슬롯만 별도로 지정한다."""
    name = filename if "/" in filename else f"ai-ver5/{filename}"
    size = svg_size(name)
    widget = QLabel()
    widget.setProperty("assetFile", name)
    widget.setPixmap(icon(name).pixmap(size))
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setFixedSize(slot, slot) if slot else widget.setFixedSize(size)
    return widget


TRON = Path(__file__).resolve().parent / "assets" / "tron"      # SETRON 보조 캐릭터 '트론' (11/11)
AVATAR_SIZE = 36


def _ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    return screen.devicePixelRatio() if screen is not None else 1.0


def tron_pixmap(name: str, height: int) -> QPixmap | None:
    """트론 그림을 높이에 맞춰 선명하게 줄인다. 파일이 없으면 None (예전 아이콘을 쓴다)."""
    source = QPixmap(str(TRON / f"{name}.png"))
    if source.isNull():
        return None
    ratio = _ratio()
    result = source.scaledToHeight(round(height * ratio), Qt.TransformationMode.SmoothTransformation)
    result.setDevicePixelRatio(ratio)
    return result


def _round_face(size: int) -> QPixmap | None:
    """동그라미 안에 트론 얼굴 (잘린 가장자리는 동그라미가 가린다)."""
    face = QPixmap(str(TRON / "tron-face.png"))
    if face.isNull():
        return None
    ratio = _ratio()
    side = round(size * ratio)
    result = QPixmap(side, side)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    circle = QPainterPath()
    circle.addEllipse(QRectF(0, 0, side, side))
    painter.fillPath(circle, ui_color("#ebeffd"))
    painter.setClipPath(circle)
    scaled = face.scaledToWidth(round(side * 0.9), Qt.TransformationMode.SmoothTransformation)
    painter.drawPixmap(round((side - scaled.width()) / 2), round(side * 0.2), scaled)      # 머리 위 파란 줄까지 보이게
    painter.end()
    result.setDevicePixelRatio(ratio)
    return result


def avatar():
    """AI 답·분석 중 카드 옆 동그라미. 트론 얼굴을 쓰고, 그림이 없으면 예전 별 아이콘."""
    face = _round_face(AVATAR_SIZE)
    if face is None:
        widget = asset_label("./9d5d2.svg", slot=AVATAR_SIZE)
        widget.setStyleSheet("background: #ebeffd; border-radius: 18px;")
    else:
        widget = QLabel()
        widget.setProperty("tronImage", "tron/tron-face.png")   # 'assetFile'이면 테마 관리자가 SVG로 다시 그려 빈 그림이 된다
        widget.setPixmap(face)
        widget.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        widget.setStyleSheet("background: transparent;")
    widget.setObjectName("aiAvatar")
    return widget


class FlowLayout(QLayout):
    """각 칩의 자연 너비를 유지하며 좁은 창에서는 줄바꿈한다."""
    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def minimumSize(self):
        return QSize(0, max((item.minimumSize().height() for item in self.items), default=0))

    def sizeHint(self):
        return QSize(600, self.heightForWidth(600))

    def _arrange(self, rect, apply):
        x, y, height = rect.x(), rect.y(), 0
        for item in self.items:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            size.setWidth(min(size.width(), rect.width()))
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x, y, height = rect.x(), y + height + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            height = max(height, size.height())
        return y - rect.y() + height


def flow_row(widgets):
    result = QWidget()
    layout = FlowLayout(result)
    for widget in widgets:
        layout.addWidget(widget)
    return result


class AnswerActions(QWidget):
    """조회 대수·전체 보기는 왼쪽, 파일 저장은 오른쪽 아래. 좁으면 저장 줄을 분리한다."""

    def __init__(self, left_widgets, right_widgets):
        super().__init__()
        self.setObjectName("chatAnswerActions")
        self.left_widgets = tuple(left_widgets)
        self.right_widgets = tuple(right_widgets)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(0, 1)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.left = self._group(self.left_widgets)
        self.right = self._group(self.right_widgets)
        self.stacked = None
        self._arrange(False)

    def _group(self, widgets):
        group = QWidget(self)
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for widget in widgets:
            row.addWidget(widget)
        if not widgets:
            group.hide()
        return group

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, stacked):
        if self.stacked == stacked:
            return
        self.stacked = stacked
        self.grid.removeWidget(self.left)
        self.grid.removeWidget(self.right)
        if self.left_widgets:
            self.grid.addWidget(self.left, 0, 0, 1, 2 if stacked else 1, Qt.AlignmentFlag.AlignLeft)
        if self.right_widgets:
            self.grid.addWidget(self.right, 1 if stacked else 0, 0 if stacked else 1,
                                1, 2 if stacked else 1, Qt.AlignmentFlag.AlignRight)
        self.updateGeometry()

    def resizeEvent(self, event):
        needed = self.left.sizeHint().width() + self.right.sizeHint().width() + self.grid.horizontalSpacing()
        self._arrange(bool(self.left_widgets and self.right_widgets) and event.size().width() < needed)
        super().resizeEvent(event)


def close_button(on_close, *, toast=False):
    widget = QPushButton()
    name = "ai-ver5/e6e51.svg" if toast else "ai-ver5/b0efa.svg"
    widget.setIcon(icon(name))
    widget.setIconSize(svg_size(name))
    widget.setFixedSize(32, 32)
    widget.setAccessibleName("닫기")
    widget.setToolTip("닫기")
    widget.setStyleSheet("QPushButton {padding: 0; border: 2px solid transparent; background: transparent;} QPushButton:focus {border-color:#3c5ce9;}")
    widget.clicked.connect(on_close)
    return widget


def selection_style():
    return """
        QCheckBox, QRadioButton { spacing: 8px; padding: 4px 0; }
        QCheckBox:focus, QRadioButton:focus { color: #3c5ce9; }
    """


class _SelectionStyle(QProxyStyle):
    """18px 슬롯 안의 체크 원본은 14px 그대로. 키·초점·클릭 처리는 Qt에 맡긴다."""
    def __init__(self, parent):
        super().__init__(QStyleFactory.create("Fusion"))
        self.setParent(parent)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight,
                      QStyle.PixelMetric.PM_ExclusiveIndicatorWidth, QStyle.PixelMetric.PM_ExclusiveIndicatorHeight):
            return 18
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element not in (QStyle.PrimitiveElement.PE_IndicatorCheckBox, QStyle.PrimitiveElement.PE_IndicatorRadioButton):
            return super().drawPrimitive(element, option, painter, widget)
        checked = bool(option.state & QStyle.StateFlag.State_On)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        center = option.rect.center()
        painter.save()
        if not enabled:
            painter.setOpacity(0.4)
        if element == QStyle.PrimitiveElement.PE_IndicatorRadioButton:
            name = "ai-ver5/eacd9.svg" if checked else "ai-ver5/f0598.svg"
        else:
            painter.setPen(ui_color("#3c5ce9" if checked else "#d5d7da"))
            painter.setBrush(ui_color("#3c5ce9" if checked else "white"))
            painter.drawRoundedRect(QRectF(center.x() - 8.5, center.y() - 8.5, 17, 17), 4, 4)
            name = "ai-ver5/330d5.svg" if checked else None
        if name:
            size = svg_size(name)
            painter.drawPixmap(center.x() - size.width() // 2, center.y() - size.height() // 2, icon(name).pixmap(size))
        painter.restore()


def install_selection_style(dialog):
    dialog._selection_style = _SelectionStyle(dialog)
    for widget in (*dialog.findChildren(QCheckBox), *dialog.findChildren(QRadioButton)):
        widget.setStyle(dialog._selection_style)


class VerdictDelegate(QStyledItemDelegate):
    COLORS = {"danger": ("#f8e4e7", "#c23345"), "warning": ("#f6eede", "#b88000"),
              "success": ("#e0efe8", "#1f8c59")}

    def paint(self, painter, option, index):
        tone = index.data(Qt.ItemDataRole.UserRole)
        if tone not in self.COLORS:
            return super().paint(painter, option, index)
        base = QStyleOptionViewItem(option)
        self.initStyleOption(base, index)
        text = base.text
        base.text = ""                    # 배경의 원문과 배지 글자가 겹치지 않게 한다.
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, base, painter, option.widget)
        painter.save()
        painter.setFont(base.font)
        width = min(option.rect.width() - 10, base.fontMetrics.horizontalAdvance(text) + 20)
        box = QRectF(option.rect.center().x() - width / 2, option.rect.center().y() - 12, width, 24)
        bg, fg = self.COLORS[tone]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ui_color(bg))
        painter.drawRoundedRect(box, 5, 5)
        painter.setPen(ui_color(fg))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class SavedFileToast(QFrame):
    """실제 저장 성공 이후에만 생성. 열기는 사용자가 눌렀을 때만 한다."""
    def __init__(self, paths, title, parent=None):
        super().__init__(parent)
        self.paths = tuple(Path(p) for p in paths)
        self.setObjectName("chatSavedFile")
        self.setStyleSheet("QFrame#chatSavedFile { background: #eaf4ee; border: 1px solid #b8d9c5; border-radius: 12px; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.addWidget(asset_label("62b26.svg", slot=44))
        text = QVBoxLayout()
        text.addWidget(label(title, 14, medium=True))
        names = " · ".join(f"{p.name} ({p.stat().st_size:,} B)" for p in self.paths if p.is_file())
        self.details = label(f"{names}\n{self.paths[0].parent}", 12, color=SECONDARY, wrap=True)
        from PySide6.QtWidgets import QSizePolicy
        self.details.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self.details)
        row.addLayout(text, 1)
        self.folder_button = button("폴더 열기")
        self.file_button = button("파일 열기")
        self.folder_button.clicked.connect(lambda: self._open(self.paths[0].parent))
        self.file_button.clicked.connect(lambda: self._open(self.paths[0]))
        row.addWidget(self.folder_button)
        row.addWidget(self.file_button)
        row.addWidget(close_button(self.hide, toast=True))

    def _open(self, path):
        if not path.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.details.setText("파일 또는 폴더를 열지 못했습니다. 저장 위치를 확인해 주세요.")
