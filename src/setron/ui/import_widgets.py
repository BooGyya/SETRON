"""VER5 가져오기 표시 부품. 원본·업무 판정·DB는 변경하지 않는다."""

from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCalendarWidget, QComboBox, QDialog, QHBoxLayout, QLineEdit, QMenu,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QWidgetAction, QHeaderView,
)

from setron.ui.components import Card, button, label
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import ASSETS, INK, PRIMARY, SECONDARY, icon, svg_size


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
    """문자 입력과 달력 선택을 함께 제공한다. 공란을 오늘로 자동 확정하지 않는다."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.calendar_action = self.addAction(icon("import-ver5/fdb91.svg"), QLineEdit.ActionPosition.TrailingPosition)
        self.calendar_action.setToolTip("달력에서 날짜 선택")
        self.calendar_action.setText("달력에서 날짜 선택")
        self.calendar_action.triggered.connect(self.open_calendar)
        self.setPlaceholderText("YYYY-MM-DD")
        self.setStyleSheet("QLineEdit {background:#f9fafb;border:1px solid #d5d7da;border-radius:8px;padding:0 10px;}"
                           "QLineEdit:focus {border:2px solid #3c5ce9;}")

    def open_calendar(self):
        if self.isReadOnly() or not self.isEnabled():
            return
        menu = QMenu(self)
        calendar = QCalendarWidget(menu)
        calendar.setGridVisible(True)
        current = QDate.fromString(self.text(), "yyyy-MM-dd")
        calendar.setSelectedDate(current if current.isValid() else QDate.currentDate())
        action = QWidgetAction(menu)
        action.setDefaultWidget(calendar)
        menu.addAction(action)
        calendar.clicked.connect(lambda day: (self.setText(day.toString("yyyy-MM-dd")), menu.close()))
        self.calendar_menu = menu
        menu.popup(self.mapToGlobal(self.rect().bottomLeft()))


class ImportSteps(QWidget):
    """실제로 완료한 단계만 체크한다. 단계 선택은 해당 카드로 스크롤할 뿐이다."""

    jump_requested = Signal(int)
    TITLES = ("시트 선택", "원본 미리보기", "AI 추출 검토", "변경분 확인 · 저장")

    def __init__(self):
        super().__init__()
        self.buttons = []
        self.markers, self.captions = [], []
        row = QVBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for index, title in enumerate(self.TITLES):
            item = button("")
            contents = QHBoxLayout(item)
            contents.setContentsMargins(8, 4, 8, 4)
            contents.setSpacing(4)
            marker = label(str(index + 1), 13)
            marker_font = QFont("Segoe UI Symbol")
            marker_font.setPixelSize(13)
            marker.setFont(marker_font)
            caption = label(title, 12, medium=True)
            for child in (marker, caption):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                contents.addWidget(child)
            self.markers.append(marker)
            self.captions.append(caption)
            item.setMinimumWidth(contents.sizeHint().width())
            item.setFixedHeight(30)
            item.setAccessibleName(f"{index + 1}단계 {title}로 이동")
            item.clicked.connect(lambda checked=False, i=index: self.jump_requested.emit(i))
            self.buttons.append(item)
        row.addWidget(_WrapRow(self.buttons, 145))
        self.set_stage(0)

    def set_stage(self, stage):
        self.stage = stage
        for index, item in enumerate(self.buttons):
            done = index < stage
            self.markers[index].setText("✓" if done else str(index + 1))
            bg, ink = ("#e5f5eb", "#1f8c59") if done else ("#e8edfc", PRIMARY) if index == stage else ("#f4f5f8", SECONDARY)
            item.setStyleSheet(f"QPushButton {{background:{bg};color:{ink};border:0;border-radius:12px;padding:4px 8px;}}"
                              f"QPushButton:focus {{border:1px solid {PRIMARY};}}")
            for text in (self.markers[index], self.captions[index]):
                text.setStyleSheet(f"color:{ink};")


@dataclass(frozen=True)
class ImportComparison:
    """같은 시트·Site 범위로 확인한 직전 저장본 비교의 표시 계약. 숫자를 UI에서 추정하지 않는다."""

    previous_date: date
    scope_label: str
    changed: int
    added: int
    unchanged: int
    absent: int
    details: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.previous_date, date) or not isinstance(self.scope_label, str) or not self.scope_label.strip():
            raise ValueError("비교 기준일과 범위가 필요합니다.")
        if any(type(v) is not int or v < 0 for v in (self.changed, self.added, self.unchanged, self.absent)):
            raise ValueError("비교 대수는 0 이상의 정수여야 합니다.")
        if not isinstance(self.details, tuple) or any(not isinstance(t, str) for t in self.details):
            raise ValueError("비교 상세 형식이 올바르지 않습니다.")


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
        self.dates = QComboBox()
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
