"""VER5 가져오기 표시 부품. 원본·업무 판정·DB는 변경하지 않는다."""

from PySide6.QtCore import QDate, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QTextCharFormat, QTextLayout
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLineEdit,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QSizePolicy,
    QStyle, QStyleOptionFrame, QTabBar, QToolButton,
)

from setron.features.excel_import.schemas import ImportComparison
from setron.ui.appearance import set_color_style, ui_color
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, button, label
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import ASSETS, INK, LINE, PRIMARY, SECONDARY, TONES, icon, svg_size


class ImportTabs(QTabBar):
    """회색 기준선과 선택 밑줄을 같은 좌표에 그리는 가져오기 전용 탭."""

    def __init__(self):
        super().__init__()
        self.setExpanding(False)
        self.setDrawBase(False)
        set_color_style(self, lambda colors: (
            "QTabBar {background:transparent;border:0;}"
            f"QTabBar::tab {{background:transparent;border:0;border-radius:0;margin:0;"
            f"padding:8px 10px;color:{colors['muted']};}}"
            f"QTabBar::tab:selected, QTabBar::tab:hover {{color:{colors['accent']};}}"))

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        baseline = QRectF(0, self.height() - 2, self.width(), 2)
        painter.fillRect(baseline, ui_color(LINE))
        if self.currentIndex() >= 0:
            selected = self.tabRect(self.currentIndex())
            painter.fillRect(QRectF(selected.left(), baseline.top(), selected.width(), baseline.height()),
                             ui_color(PRIMARY))


def import_combo_style():
    """Figma 원본 16px 화살표를 기존 Qt 콤보 컨트롤에 배치한다."""
    path = (ASSETS / "import-ver5/ba8e3.svg").as_posix()
    return ("QComboBox {padding-right:28px;} QComboBox::drop-down {border:0;width:28px;}"
            f"QComboBox::down-arrow {{image:url({path});width:16px;height:16px;}}")


def picture(name):
    """SVG의 루트 크기를 그대로 유지한다."""
    result = label("")
    result.setFixedSize(svg_size(f"import-ver5/{name}"))
    result.setPixmap(icon(f"import-ver5/{name}").pixmap(result.size()))
    result.setProperty("assetFile", f"import-ver5/{name}")
    return result


class DateField(QLineEdit):
    """숫자 8자리 입력과 달력 선택. 구분자를 자동 표시하며 공란을 오늘로 확정하지 않는다."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        # 입력·붙여넣기·삭제·되돌리기는 Qt가 처리하고 숫자 8칸과 구분자만 허용한다.
        self.setInputMask("0000-00-00;_")
        self.dialog = None
        self.calendar_action = self.addAction(icon("import-ver5/fdb91.svg"), QLineEdit.ActionPosition.TrailingPosition)
        self.calendar_action.setToolTip("달력에서 날짜 선택")
        self.calendar_action.setText("달력에서 날짜 선택")
        self.calendar_action.triggered.connect(self.open_calendar)
        self.setPlaceholderText("YYYY-MM-DD")
        self.setStyleSheet("QLineEdit {background:#f9fafb;border:1px solid #d5d7da;border-radius:8px;padding:0 10px;}"
                           "QLineEdit:focus {border:2px solid #3c5ce9;}")

    def text(self):
        # 입력 마스크의 고정 구분자 '--'는 입력값이나 날짜로 취급하지 않는다.
        value = super().text()
        return value if value.replace("-", "") else ""

    def open_calendar(self):
        if self.isReadOnly() or not self.isEnabled():
            return
        from setron.ui.date_range import DateRangeDialog

        if self.dialog is not None:
            if self.dialog.isVisible():
                self.dialog.reject()
                return
            self.dialog.deleteLater()
        current = QDate.fromString(self.text(), "yyyy-MM-dd")
        day = (current if current.isValid() else QDate.currentDate()).toPython()
        dialog = DateRangeDialog(day, day, single_day=True, parent=self)
        self.dialog = dialog
        dialog.accepted.connect(lambda: self.setText(dialog.end.isoformat()) if dialog is self.dialog else None)
        dialog.open_at(self)

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key.Key_F4 or
                event.key() == Qt.Key.Key_Down and event.modifiers() & Qt.KeyboardModifier.AltModifier):
            self.open_calendar()
            event.accept()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event):
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.reject()
        super().hideEvent(event)


class PeriodDateField(DateField):
    """기준일·기간 입력: 클릭은 첫 자리부터, 편집은 Qt 기본 동작, 블록 커서만 생략한다.

    ``open_on_click``을 켜면 칸 어디를 눌러도 달력이 열린다 (가져오기 기준일, 11-10 2차 6-2). 키보드 입력은 그대로다.
    """

    def __init__(self, text="", parent=None, *, open_on_click=False):
        super().__init__(text, parent)
        self.open_on_click = open_on_click

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._press_position = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton and not self.isReadOnly():
            self.setCursorPosition(0)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if (event.button() == Qt.MouseButton.LeftButton and not self.isReadOnly()
                and event.position().toPoint() == getattr(self, "_press_position", None)):
            self.setCursorPosition(0)
            if self.open_on_click:
                self.open_calendar()

    def paintEvent(self, event):
        # 입력 마스크는 커서를 한 글자 크기의 검은 선택 배경으로 그린다.
        # 값·포커스·입력 상태를 건드리지 않고 같은 Qt 프레임과 텍스트를 커서 없이 그린다.
        panel = QStyleOptionFrame()
        self.initStyleOption(panel)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_PanelLineEdit, panel, painter, self)
        contents = self.style().subElementRect(QStyle.SubElement.SE_LineEditContents, panel, self)
        contents = contents.marginsRemoved(self.textMargins())
        # 기존 달력 액션의 실제 위치를 사용해 글자와 아이콘이 겹치지 않게 한다.
        for action in self.findChildren(QToolButton):
            if action.isVisibleTo(self):
                contents.setRight(min(contents.right(), action.x() - 4))
        contents.adjust(2, 0, -2, 0)
        layout = QTextLayout(self.displayText(), self.font())
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(max(0, contents.width()))
        layout.endLayout()
        selections = []
        if self.hasSelectedText():
            selected = QTextLayout.FormatRange()
            selected.start = self.selectionStart()
            selected.length = self.selectionEnd() - self.selectionStart()
            selected.format = QTextCharFormat()
            selected.format.setForeground(panel.palette.highlightedText())
            selected.format.setBackground(panel.palette.highlight())
            selections.append(selected)
        origin = QPointF(contents.left(), contents.top() + (contents.height() - line.height()) / 2)
        if not self.text() and not self.hasFocus() and self.placeholderText():
            # 비어 있고 입력 중이 아니면 입력 틀(____-__-__) 대신 흐린 안내 글(YYYY-MM-DD)을 그린다.
            hint = QTextLayout(self.placeholderText(), self.font())
            hint.beginLayout()
            hint_line = hint.createLine()
            hint_line.setLineWidth(max(0, contents.width()))
            hint.endLayout()
            painter.setPen(panel.palette.placeholderText().color())
            hint.draw(painter, origin, [], QRectF(contents))
            return
        painter.setPen(panel.palette.text().color())
        layout.draw(painter, origin, selections, QRectF(contents))


class ImportSteps(QWidget):
    """실제로 완료한 단계만 체크한다. 단계 선택은 해당 카드로 스크롤할 뿐이다."""

    jump_requested = Signal(int)
    TITLES = ("시트 선택", "원본 미리보기", "AI 추출 검토", "변경분 확인 · 저장")

    def __init__(self, *, titles=None, show_completion=True):
        super().__init__()
        self.titles = tuple(titles) if titles is not None else self.TITLES
        self.show_completion = show_completion
        self.buttons = []
        self.markers, self.captions, self.completions = [], [], []
        row = QVBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for index, title in enumerate(self.titles):
            item = button("")
            item.setCheckable(not show_completion)
            contents = QHBoxLayout(item)
            contents.setContentsMargins(12, 4, 12, 4)
            contents.setSpacing(6)
            contents.setAlignment(Qt.AlignmentFlag.AlignCenter)
            marker = label(str(index + 1), 13)
            marker.setFixedWidth(12)
            marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption = label(title, 12, medium=True)
            complete = label("완료", 10, medium=True)
            for child in (marker, caption, complete):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                # 남는 버튼 폭을 글자 사이에 분배하지 않고 한 묶음으로 배치한다.
                child.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
                contents.addWidget(child)
            self.markers.append(marker)
            self.captions.append(caption)
            self.completions.append(complete)
            item.setMinimumWidth(contents.sizeHint().width())
            item.setFixedHeight(34)
            item.setAccessibleName(f"{index + 1}단계 {title}로 이동")
            item.clicked.connect(lambda checked=False, i=index: self.jump_requested.emit(i))
            self.buttons.append(item)
        row.addWidget(_WrapRow(self.buttons, 145))
        self.set_stage(0)

    def set_stage(self, stage):
        self.stage = stage
        for index, item in enumerate(self.buttons):
            done = self.show_completion and index < stage
            self.markers[index].setText(str(index + 1))
            self.completions[index].setVisible(False)   # '완료' 글자 없이 색으로만 구분한다 (11-10 2차 2-1)
            state = ("완료" if done else "진행 중" if index == stage else "대기") if self.show_completion else (
                "선택됨" if index == stage else "이동")
            item.setAccessibleName(f"{index + 1}단계 {self.titles[index]} · {state}")
            if item.isCheckable():
                item.setChecked(index == stage)
            bg, ink = ("#e5f5eb", "#1f8c59") if done else ("#e8edfc", PRIMARY) if index == stage else ("#f4f5f8", SECONDARY)
            item.setStyleSheet(f"QPushButton {{background:{bg};color:{ink};border:0;border-radius:12px;padding:4px 8px;}}"
                              f"QPushButton:focus {{border:1px solid {PRIMARY};}}")
            for text in (self.markers[index], self.captions[index], self.completions[index]):
                text.setStyleSheet(f"color:{ink};")


class Toast(QFrame):
    """창 위쪽 가운데에 잠깐 떴다 사라지는 알림. 누르면 바로 닫힌다.

    이미 떠 있는 알림이 있으면 새 문구를 아래 줄에 붙이고, 더 심각한 색을 쓴다.
    """

    ORDER = ("success", "info", "warning", "danger")

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("importToast")
        self.tone = "info"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        self.text = label("", 14, medium=True, wrap=True)
        self.text.setMaximumWidth(760)
        layout.addWidget(self.text)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text, tone="success", milliseconds=3500):
        if not self.isHidden() and self.text.text():
            text = "\n".join((self.text.text(), text))
            tone = max(tone, self.tone, key=self.ORDER.index)
        self.tone = tone
        background, ink, border = TONES[tone]
        self.setStyleSheet(f"QFrame#importToast {{background:{background};border:1px solid {border};border-radius:10px;}}")
        self.text.setStyleSheet(f"color:{ink};")
        self.text.setText(text)
        # 줄바꿈 글상자는 좁게 잡히므로, 가장 긴 줄 길이에 맞춰 폭을 정한다 (최대 760px).
        metrics = self.text.fontMetrics()
        self.text.setFixedWidth(min(760, max(metrics.horizontalAdvance(line) for line in text.split("\n")) + 8))
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move(max(0, (parent.width() - self.width()) // 2), 24)   # 제목 줄 높이: 단계 칩을 가리지 않는다
        self.raise_()
        self.show()
        self.timer.start(milliseconds)

    def mousePressEvent(self, event):
        self.hide()
        super().mousePressEvent(event)


class ComparisonCard(Card):
    """비교 자료 미연결을 0대로 표시하지 않고, 실제 비교 결과만 보여 준다."""

    def __init__(self):
        super().__init__(padding=16)
        self.setStyleSheet("QFrame#card {background:#f4f5f8;border:0;border-radius:10px;}")
        self.title = label("직전 저장본과 비교", 13, medium=True, wrap=True)
        self.body.addWidget(self.title)
        self.counts = QWidget()
        self.count_layout = QHBoxLayout(self.counts)
        self.count_layout.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.counts)
        self.details = label("", 12, color=SECONDARY, wrap=True)
        self.body.addWidget(self.details)
        self.show_comparison(None)

    def show_comparison(self, value, *, message=None):
        self.comparison = value
        while self.count_layout.count():
            old = self.count_layout.takeAt(0).widget()
            if old is not None:
                old.deleteLater()
        self.counts.setVisible(value is not None)
        if value is None:
            self.title.setText("직전 저장본과 비교")
            self.details.setText(message or "같은 범위의 직전 저장본 비교 자료가 없습니다. 대수를 임의로 계산하지 않습니다.")
            return
        self.title.setText(f"직전 저장본({value.previous_date.isoformat()} · {value.scope_label})과 비교")
        for title, count, ink in (("버전 바뀜", value.changed, PRIMARY), ("새로 추가", value.added, INK),
                                  ("변경 없음", value.unchanged, INK), ("이번 자료에 없음", value.absent, "#c23345")):
            item = QWidget()
            box = QVBoxLayout(item)
            box.setContentsMargins(0, 0, 12, 0)
            box.addWidget(label(title, 11, color=SECONDARY, wrap=True))
            box.addWidget(label(f"{count}대", 16, medium=True, color=ink))
            self.count_layout.addWidget(item)
        self.details.setText(" · ".join((*value.details, "이번 자료에 없는 설비의 과거 기록을 자동 삭제하지 않습니다.")))


class ImportDataStateDialog(QDialog):
    """미등록·빈 버전·미관측·추출 실패를 구분하는 VER5 상태 안내."""

    date_selected = Signal(object)
    import_requested = Signal()

    def __init__(self, dates=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("데이터 상태별 표시")
        self.setStyleSheet("QDialog {background:white;}")
        self.resize(760, 480)
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 22, 24, 20)
        body.setSpacing(16)
        body.addWidget(label("데이터 상태별 표시", 20, medium=True))
        body.addWidget(label("선택한 날짜에 현황이 없으면 다른 날짜 자료로 자동 대체하지 않습니다.", 13, wrap=True))
        self.dates = ComboBox()
        self.dates.setAccessibleName("저장된 기준일 선택")
        self.dates.setProperty("queryField", True)
        self.dates.setStyleSheet(import_combo_style())
        self.dates.setFixedHeight(38)
        for day in sorted(set(dates), reverse=True):
            self.dates.addItem(day.isoformat(), day)
        choose = button("저장된 날짜 선택")
        choose.setEnabled(bool(dates))
        choose.clicked.connect(self._choose)
        go = button("현황표 가져오기", primary=True)
        go.clicked.connect(lambda: (self.import_requested.emit(), self.accept()))
        row = QHBoxLayout()
        row.addWidget(self.dates)
        row.addWidget(choose)
        row.addWidget(go)
        body.addLayout(row)
        table = QTableWidget(4, 3)
        table.setHorizontalHeaderLabels(["상태", "화면 표시", "처리 기준"])
        values = (("설비가 없는 조합", "해당 없음", "설비 0대 · 적용률 —"),
                  ("설비는 있으나 버전 없음", "확인 필요", "목표 달성으로 계산하지 않음"),
                  ("해당 날짜 스냅샷 없음", "미관측", "다른 날짜를 자동 복제하지 않음"),
                  ("AI/Excel 추출 실패", "다시 시도 · 원본 확인", "검토·확정 전에는 저장하지 않음"))
        for r, values_row in enumerate(values):
            for c, text in enumerate(values_row):
                table.setItem(r, c, QTableWidgetItem(text))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(52)
        table.setShowGrid(False)
        table.setStyleSheet("QTableWidget {border:1px solid #dee0e5;border-radius:8px;background:white;}"
                           "QHeaderView::section {border:0;background:#f4f5f8;color:#5b6880;padding:8px;}"
                           "QTableWidget::item {border-bottom:1px solid #eceef1;padding:8px;}")
        body.addWidget(table, 1)
        close = button("닫기")
        close.clicked.connect(self.reject)
        body.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _choose(self):
        if self.dates.currentData() is not None:
            self.date_selected.emit(self.dates.currentData())
            self.accept()
