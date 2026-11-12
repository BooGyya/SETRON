"""완료한 초기화 단계만 진행률에 반영하는 시작 화면."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMainWindow, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.appearance import manager, set_color_style
from setron.ui.components import label
from setron.ui.main_window import BrandLogo
from setron.ui.read_worker import ReadRequest


class StartupWindow(QMainWindow):
    closed = Signal()
    STEPS = ("settings", "source", "window", "query")
    NEXT_MESSAGES = ("설정 확인 중", "자료 불러오는 중", "화면 준비 중", "조회 결과 준비 중", "실행 준비 완료")

    def __init__(self, window_icon=None):
        super().__init__()
        self.accepting, self.completed_steps = True, 0
        self.request = ReadRequest(self)
        self.setObjectName("startupWindow")
        self.setWindowTitle("SETRON")
        if window_icon is not None:
            self.setWindowIcon(window_icon)
        self.setMinimumSize(960, 600)
        self.resize(1440, 960)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        content = QWidget()
        content.setObjectName("startupSurface")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        set_color_style(content, lambda colors: f"QWidget#startupSurface {{background:{colors['canvas']};}}")
        body = QVBoxLayout(content)
        body.setContentsMargins(32, 32, 32, 32)
        body.addStretch()

        self.card = QFrame()
        self.card.setObjectName("startupCard")
        self.card.setFixedWidth(440)
        self.card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        set_color_style(self.card, lambda colors: (
            f"QFrame#startupCard {{background:{colors['surface']};border:1px solid {colors['line']};border-radius:20px;}}"))
        card_body = QVBoxLayout(self.card)
        card_body.setContentsMargins(36, 32, 36, 32)
        card_body.setSpacing(0)
        self.logo = BrandLogo(size=QSize(260, 80))
        self.logo.setObjectName("startupLogo")
        card_body.addWidget(self.logo, alignment=Qt.AlignmentFlag.AlignHCenter)
        card_body.addSpacing(28)

        status = QHBoxLayout()
        status.setContentsMargins(0, 0, 0, 0)
        status.setSpacing(12)
        self.message = label(self.NEXT_MESSAGES[0], 15, medium=True)
        self.message.setObjectName("startupLoadingMessage")
        self.message.setWordWrap(False)
        self.message.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.message.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.percentage = label("0% · 0/4", 13, numeric=True)
        self.percentage.setObjectName("startupProgressValue")
        self.percentage.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percentage.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        set_color_style(self.percentage, lambda colors: f"color:{colors['muted']};")
        status.addWidget(self.message, 1)
        status.addWidget(self.percentage)
        card_body.addLayout(status)
        card_body.addSpacing(14)

        self.progress = QProgressBar()
        self.progress.setObjectName("startupProgress")
        self.progress.setRange(0, len(self.STEPS))
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setAccessibleName("완료된 초기화 단계")
        self.progress.setToolTip("설정 확인 · 자료 읽기 · 화면 준비 · 첫 조회의 실제 완료 단계 기준")
        set_color_style(self.progress, lambda colors: (
            f"QProgressBar#startupProgress {{background:{colors['selected']};border:0;border-radius:4px;padding:0;}}"
            f"QProgressBar#startupProgress::chunk {{background:{colors['accent']};border:0;border-radius:4px;}}"))
        card_body.addWidget(self.progress)
        body.addWidget(self.card, alignment=Qt.AlignmentFlag.AlignHCenter)
        body.addStretch()
        self.setCentralWidget(content)
        if controller := manager():
            controller.changed.connect(self._fit_text)
        self._fit_text()

    def _fit_text(self):
        # 폰트 배율과 DPI가 바뀌어도 한 줄 전체와 위·아래 여유를 확보한다.
        for text in (self.message, self.percentage):
            text.setMinimumHeight(text.fontMetrics().height() + 8)
        longest = max(self.message.fontMetrics().horizontalAdvance(text) for text in self.NEXT_MESSAGES)
        count_width = self.percentage.fontMetrics().horizontalAdvance("100% · 4/4")
        self.card.setFixedWidth(max(440, longest + count_width + 12 + 72))
        self.card.updateGeometry()

    def complete_step(self, name):
        """실제 작업이 끝난 순서대로 호출한다. 타이머로 값을 증가시키지 않는다."""
        if not self.accepting:
            return False
        completed = self.STEPS.index(name) + 1
        if completed <= self.completed_steps:
            return False
        if completed != self.completed_steps + 1:
            raise ValueError("초기화 단계는 완료된 순서대로 반영해야 합니다.")
        self.completed_steps = completed
        self.progress.setValue(completed)
        self.percentage.setText(f"{completed * 100 // len(self.STEPS)}% · {completed}/{len(self.STEPS)}")
        self.message.setText(self.NEXT_MESSAGES[completed])
        self.progress.setAccessibleDescription(f"초기화 {completed}/{len(self.STEPS)} 단계 완료")
        self._fit_text()
        return True

    def closeEvent(self, event):
        if self.accepting:
            self.accepting = False
            self.request.cancel()
            self.closed.emit()
        super().closeEvent(event)
