"""업무 화면과 무관한 공통 표시 컴포넌트. 필터·집계·목표 대화상자는 후속 작업이다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTransform
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from setron.ui.theme import ASSETS, LINE, SECONDARY, SOFT, TONES, font, icon, svg_size, set_widget_font


def label(text: str, size: int = 14, *, medium: bool = False,
          color: str | None = None, numeric: bool = False, wrap: bool = False) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    set_widget_font(result, font(size, medium=medium, numeric=numeric))
    result.setWordWrap(wrap)
    if color:
        result.setStyleSheet(f"color: {color};")
    return result


def button(text: str, *, primary: bool = False, asset: str | None = None) -> QPushButton:
    result = QPushButton(text)
    result.setProperty("primary", primary)
    set_widget_font(result, font(14, medium=True))
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if asset:
        result.setIcon(icon(asset))
        result.setIconSize(svg_size(asset))
        result.setText(" " + text)
        result.setAccessibleName(text)
    return result


def filter_checkbox(text: str) -> QCheckBox:
    result = QCheckBox(text)
    result.setAccessibleName(text)
    set_widget_font(result, font(13, medium=True))
    result.setMinimumHeight(20)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    tick = (ASSETS / "checkbox-tick.svg").as_posix()
    result.setStyleSheet(
        "QCheckBox {spacing:6px;padding:0 4px;}"
        f"QCheckBox::indicator {{width:14px;height:14px;border:1px solid {SECONDARY};border-radius:4px;background:white;}}"
        "QCheckBox:disabled {color:#8b95a8;}"
        f"QCheckBox::indicator:disabled {{background:{SOFT};border-color:{LINE};}}"
        f"QCheckBox::indicator:checked {{background:#3655c7;border-color:#3655c7;image:url({tick});}}")
    return result


def back_button(description="이전 화면으로 돌아가기", *, size=36) -> QPushButton:
    result = button("", asset="arrow-left.svg")
    result.setText("")
    result.setAccessibleName(description)
    result.setToolTip(description)
    result.setFixedSize(size, size)
    result.setStyleSheet("QPushButton {padding:0;}")
    return result


class DisclosureArrow(QLabel):
    """원본 16px 셰브론을 접힘/펼침 방향으로 표시한다. 접어도 슬롯은 유지한다."""

    def __init__(self, *, expanded=False):
        super().__init__()
        asset = "c9be0.svg"
        size = svg_size(asset)
        self.set_arrow_pixmap(icon(asset).pixmap(size))
        self.setFixedSize(size)
        self.setProperty("assetFile", asset)
        self.set_expanded(expanded)

    def set_arrow_pixmap(self, pixmap):
        self._up = pixmap
        self._down = pixmap.transformed(QTransform().rotate(180), Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(self._up if getattr(self, "expanded", False) else self._down)

    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        self.setPixmap(self._up if self.expanded else self._down)
        self.setAccessibleName("접기" if self.expanded else "펼치기")


class Card(QFrame):
    def __init__(self, *, padding: int = 24, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(16)


class StatusBadge(QLabel):
    """전달받은 문구만 표시한다. 배지 색상으로 업무 판정을 만들지 않는다."""

    def __init__(self, text: str, tone: str = "neutral"):
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        set_widget_font(self, font(12, medium=True))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        if tone not in TONES:
            raise ValueError(f"지원하지 않는 배지 색상입니다: {tone}")
        self.tone = tone
        background, ink, border = TONES[tone]
        self.setStyleSheet(
            f"color:{ink};background:{background};border:1px solid {border};"
            "border-radius:4px;padding:3px 8px;"
        )


class EmptyState(Card):
    def __init__(self, title: str, description: str):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.body.addWidget(label(title, 20, medium=True, wrap=True))
        self.body.addWidget(label(description, color=SECONDARY, wrap=True))


class PageHeader(QWidget):
    """표시와 액션 슬롯만 제공한다. 실제 조회·내보내기·설정 처리는 담당 화면이 연결한다."""

    action_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.title = label("대시보드", 30, medium=True)
        title_font = font(30, medium=True)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.6)
        set_widget_font(self.title, title_font)
        self.back = back_button()
        self.heading = QWidget()
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(12)
        heading.addWidget(self.back)
        heading.addWidget(self.title, 1)
        self.actions = {}
        self.toolbar = QWidget()
        actions = QHBoxLayout(self.toolbar)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(16)
        for key, text, asset in (
            ("equipment_list", "설비 목록", None),
            ("export", "데이터 내보내기", "ef2c5.svg"),
            ("settings", "화면 설정", "b02b9.svg"),
        ):
            action = button(text, asset=asset)
            action.setObjectName("action_" + key)
            action.setFixedHeight(40 if key in ("equipment_list", "export") else 36)
            action.setEnabled(False)
            action.setToolTip("담당 기능을 연결한 뒤 활성화됩니다.")
            action.clicked.connect(lambda checked=False, action_key=key: self.action_requested.emit(action_key))
            actions.addWidget(action)
            self.actions[key] = action
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
        self.grid.setColumnStretch(0, 1)
        self._compact = None
        self._reflow(False)

    def _reflow(self, compact: bool):
        if self._compact == compact:
            return
        self.grid.removeWidget(self.heading)
        self.grid.removeWidget(self.toolbar)
        self.grid.addWidget(self.heading, 0, 0)
        self.grid.addWidget(self.toolbar, 1 if compact else 0, 0 if compact else 1,
                            alignment=Qt.AlignmentFlag.AlignLeft if compact else Qt.AlignmentFlag.AlignRight)
        self._compact = compact

    def resizeEvent(self, event):
        self._reflow(event.size().width() < 1100)
        super().resizeEvent(event)
