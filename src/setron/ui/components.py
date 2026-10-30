"""업무 화면과 무관한 공통 표시 컴포넌트. 필터·집계·목표 대화상자는 후속 작업이다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from setron.ui.theme import SECONDARY, TONES, font, icon, svg_size


def label(text: str, size: int = 14, *, medium: bool = False,
          color: str | None = None, numeric: bool = False, wrap: bool = False) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setFont(font(size, medium=medium, numeric=numeric))
    result.setWordWrap(wrap)
    if color:
        result.setStyleSheet(f"color: {color};")
    return result


def button(text: str, *, primary: bool = False, asset: str | None = None) -> QPushButton:
    result = QPushButton(text)
    result.setProperty("primary", primary)
    result.setFont(font(14, medium=True))
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if asset:
        result.setIcon(icon(asset))
        result.setIconSize(svg_size(asset))
        result.setText(" " + text)
        result.setAccessibleName(text)
    return result


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
        self.setFont(font(12, medium=True))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        if tone not in TONES:
            raise ValueError(f"지원하지 않는 배지 색상입니다: {tone}")
        self.tone = tone
        background, ink, border = TONES[tone]
        self.setStyleSheet(
            f"color:{ink};background:{background};border:1px solid {border};"
            "border-radius:6px;padding:3px 8px;"
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
        self.title = label("대시보드", 24, medium=True)
        self.actions = {}
        self.toolbar = QWidget()
        actions = QHBoxLayout(self.toolbar)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(16)
        for key, text, asset in (
            ("equipment_list", "선택 범위 설비 목록", None),
            ("export", "데이터 내보내기", "ef2c5.svg"),
            ("settings", "화면 설정", "b02b9.svg"),
            ("help", "ⓘ 도움말", None),
        ):
            action = button(text, asset=asset)
            action.setObjectName("action_" + key)
            action.setEnabled(key == "help")
            if key != "help":
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
        self.grid.removeWidget(self.title)
        self.grid.removeWidget(self.toolbar)
        self.grid.addWidget(self.title, 0, 0)
        self.grid.addWidget(self.toolbar, 1 if compact else 0, 0 if compact else 1,
                            alignment=Qt.AlignmentFlag.AlignLeft if compact else Qt.AlignmentFlag.AlignRight)
        self._compact = compact

    def resizeEvent(self, event):
        self._reflow(event.size().width() < 1100)
        super().resizeEvent(event)
