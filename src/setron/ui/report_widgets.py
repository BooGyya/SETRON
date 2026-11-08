"""보고서 전용 표시 부품. 기존 카드·표·달력·진행 표시를 재사용한다."""

from PySide6.QtCharts import QBarCategoryAxis, QBarSet, QChart, QChartView, QLineSeries, QPieSeries, QStackedBarSeries, QValueAxis
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QSizePolicy,
    QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from setron.ui.chat_export import chart_png, export_folder
from setron.ui.components import button, label
from setron.ui.import_dialogs import ImportBusyBar, ImportConfirmDialog, ImportDialog
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.report_export import display_value, save_generated_report
from setron.ui.theme import PRIMARY, SECONDARY, font
from setron.ui.version_management_widgets import card, note, set_rows, table, text_field

COLORS = ("#3c5ce9", "#293b66", "#9ca3af")


def field(caption, control):
    holder = QWidget()
    holder.setMinimumWidth(0)
    control.setMinimumWidth(0)
    body = QVBoxLayout(holder)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(8)
    heading = label(caption, 13, color=SECONDARY)
    heading.setBuddy(control)
    control.setAccessibleName(caption)
    body.addWidget(heading)
    body.addWidget(control)
    return holder


def step_tabs():
    result = QTabBar()
    result.addTab("1 생성 조건")
    result.addTab("2 미리보기·저장")
    result.setExpanding(False)
    result.setUsesScrollButtons(False)
    result.setAccessibleName("보고서 생성 단계")
    result.setStyleSheet("QTabBar::tab {background:transparent;border:0;border-radius:0;padding:8px 10px;"
                        "color:#5b6880;}QTabBar::tab:selected {color:#3c5ce9;border-bottom:2px solid #3c5ce9;}")
    return result


class Choices(QWidget):
    changed = Signal(str)

    def __init__(self, entries, columns=3):
        super().__init__()
        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        for i, (key, caption) in enumerate(entries):
            b = button(caption)
            b.setCheckable(True)
            b.setProperty("queryChoice", True)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda checked=False, value=key: self.changed.emit(value))
            self.group.addButton(b)
            self.buttons[key] = b
            grid.addWidget(b, i // columns, i % columns)
            grid.setColumnStretch(i % columns, 1)
        self.set_value(next(iter(self.buttons)))

    def value(self):
        return next(key for key, b in self.buttons.items() if b.isChecked())

    def set_value(self, value):
        self.buttons[value].setChecked(True)


def checkbox(text):
    result = QCheckBox(text)
    result.setChecked(True)
    result.setMinimumHeight(36)
    result.setStyleSheet("QCheckBox {background:white;border:1px solid #d5d7da;border-radius:7px;padding:0 12px;}")
    return result


class GenerationDialog(ImportDialog):
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__("보고서를 생성하고 있습니다", parent)
        self.add_message("조건·형식·내용을 고정했습니다. 완료되면 미리보기를 표시합니다.")
        self.body.addWidget(ImportBusyBar())
        self.add_action("생성 취소", self.reject, default=True)
        self.finish_layout()

    def reject(self):
        self.cancelled.emit()
        super().reject()


class ReportPreview(QWidget):
    """요약/표·그래프/원문·근거/AI 해석. 생성한 불변 객체만 표시한다."""

    def __init__(self):
        super().__init__()
        self.result = None
        self.charts = []
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        self.title = label("", 22, medium=True, wrap=True)
        self.metadata = note()
        body.addWidget(self.title)
        body.addWidget(self.metadata)
        self.tabs = Choices(tuple((str(i), title) for i, title in enumerate(("요약", "표·그래프", "원문·근거", "AI 해석"))), 4)
        body.addWidget(self.tabs)
        self.stack = QStackedWidget()
        body.addWidget(self.stack)
        self.tabs.changed.connect(lambda value: self.select_tab(int(value)))
        self.set_report(None)

    def select_tab(self, index):
        self.tabs.set_value(str(index))
        for i in range(self.stack.count()):
            self.stack.widget(i).setSizePolicy(QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored)
        self.stack.setCurrentIndex(index)
        self.stack.updateGeometry()

    def set_report(self, report):
        self.result, self.charts = report, []
        while self.stack.count():
            child = self.stack.widget(0)
            self.stack.removeWidget(child)
            child.deleteLater()
        self.title.setText(report.title if report else "")
        self.metadata.setText(f"{report.created:%Y-%m-%d %H:%M} · {report.scope}" if report else "")
        for tab in range(4):
            holder = QWidget()
            body = QVBoxLayout(holder)
            body.setContentsMargins(0, 12, 0, 0)
            body.setSpacing(16)
            if report is None:
                body.addWidget(note("아직 생성된 보고서가 없습니다. 생성 조건을 확인한 뒤 생성하세요."))
            for index, section in enumerate(report.sections if report else ()):
                if section.tab != tab:
                    continue
                body.addWidget(label(section.title, 17, medium=True, wrap=True))
                if section.note:
                    body.addWidget(note(section.note))
                if section.table is not None:
                    model, view = table(section.table.columns, section.title)
                    set_rows(model, view, tuple(tuple(display_value(c) for c in row) for row in section.table.rows))
                    height = view.horizontalHeader().height() + sum(view.rowHeight(i) for i in range(model.rowCount())) + 20
                    view.setFixedHeight(max(78, min(360, height)))
                    body.addWidget(view)
                    if not section.table.rows:
                        body.addWidget(note("조건에 맞는 자료가 없습니다."))
                if section.chart and section.table and section.table.rows:
                    kinds = ("bar", "pie", "treemap") if section.chart == "distribution" else ("trend",)
                    for kind in kinds:
                        view = TreeMap(section.table) if kind == "treemap" else data_chart(section.table, kind)
                        body.addWidget(view)
                        self.charts.append((index, view))
            body.addStretch()
            self.stack.addWidget(holder)
        self.select_tab(0)

    def pictures(self):
        # 그래프 위젯 캡처는 GUI 스레드에서만. 저장 워커에는 PNG 바이트만 전달한다.
        return tuple((index, chart_png(chart)) for index, chart in self.charts)


def data_chart(table, kind):
    chart = QChart()
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setBackgroundRoundness(0)
    chart.legend().setFont(font(12))
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setMinimumWidth(0)
    view.setFixedHeight(280)
    view.setAccessibleName(table.title + " · 같은 수치표 참조")
    if kind == "pie":
        series = QPieSeries()
        chart.setTitle("상태 구성비 (설비·계열 건)")
        for index, title in enumerate(table.columns[1:], 1):
            value = sum(row[index] for row in table.rows)
            if value:
                part = series.append(f"{title} {value}건", value)
                part.setColor(QColor(COLORS[index - 1]))
        chart.addSeries(series)
        return view
    x, y = QBarCategoryAxis(), QValueAxis()
    x.append([str(row[0]) for row in table.rows])
    y.setLabelFormat("%.0f%%" if kind == "trend" else "%d")
    series_list = []
    if kind == "trend":
        chart.setTitle("날짜별 적용률 추이")
        y.setRange(0, 100)
        # 미확인 분모의 날짜에서 선을 끊는다. 점 하나는 점만 보인다.
        current = None
        for index, row in enumerate(table.rows):
            if row[4] is None:
                current = None
                continue
            if current is None:
                current = QLineSeries()
                current.setName("N 이상 적용률")
                current.setPen(QPen(QColor(PRIMARY), 2))
                current.setPointsVisible(True)
                series_list.append(current)
            current.append(index, float(row[4]) * 100)
        chart.legend().hide()
    else:
        chart.setTitle("계열별 구성 (설비·계열 건)")
        y.setRange(0, max(1, max(sum(row[1:]) for row in table.rows)))
        series = QStackedBarSeries()
        for i, title in enumerate(table.columns[1:], 1):
            values = QBarSet(title)
            values.setColor(QColor(COLORS[i - 1]))
            values.append([row[i] for row in table.rows])
            series.append(values)
        series_list.append(series)
    chart.addAxis(x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(y, Qt.AlignmentFlag.AlignLeft)
    for series in series_list:
        chart.addSeries(series)
        series.attachAxis(x)
        series.attachAxis(y)
    return view


class TreeMap(QWidget):
    """면적=건수인 계열별 스트립 트리맵. 원문 수치표가 키보드 대체 수단이다."""

    def __init__(self, table):
        super().__init__()
        self.table = table
        self.setMinimumWidth(0)
        self.setFixedHeight(210)
        self.setAccessibleName("트리맵 · 면적은 설비·계열 건수에 비례")
        self.setAccessibleDescription(" · ".join(str(row) for row in table.rows))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        total = sum(sum(row[1:]) for row in self.table.rows)
        if not total:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "자료 없음")
            return
        x = 0.0
        for row in self.table.rows:
            count = sum(row[1:])
            if not count:
                continue
            width = self.width() * count / total
            y = 0.0
            for index, value in enumerate(row[1:]):
                if not value:
                    continue
                height = self.height() * value / count
                rect = QRectF(x, y, width, height)
                painter.fillRect(rect, QColor(COLORS[index]))
                painter.setPen(QPen(QColor("white"), 1))
                painter.drawRect(rect)
                if width > 100 and height > 38:
                    painter.drawText(rect.adjusted(8, 4, -8, -4), Qt.AlignmentFlag.AlignCenter,
                                     f"{row[0]} · {self.table.columns[index + 1]} {value}건")
                y += height
            x += width


class ReportSaveDialog(ImportDialog):
    saved = Signal(object)

    def __init__(self, report, pictures=(), parent=None):
        super().__init__("보고서 파일 저장", parent)
        self.report, self.pictures, self.busy = report, pictures, False
        self.request = ReadRequest(self)
        self.request.completed.connect(self._finished)
        self.body.addWidget(field("생성 완료된 파일", text_field("생성 완료된 파일", report.filename, readonly=True)))
        self.folder = text_field("저장 위치", str(export_folder()))
        browse = button("위치 선택")
        browse.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        self.body.addWidget(label("저장 위치", 13, color=SECONDARY))
        self.body.addLayout(row)
        self.status = note("생성 당시 조건·내용을 파일로 저장합니다.")
        self.body.addWidget(self.status)
        self.progress = ImportBusyBar()
        self.progress.hide()
        self.body.addWidget(self.progress)
        self.cancel_button = self.add_action("취소", self.reject, default=True)
        self.retry_button = self.add_action("다른 위치로 재시도", self.retry)
        self.retry_button.hide()
        self.save_button = self.add_action("저장", self.save, primary=True)
        self.browse_button = browse
        self.finish_layout()

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, "보고서 저장 위치", self.folder.text())
        if folder:
            self.folder.setText(folder)
            return True
        return False

    def retry(self):
        if self.browse():
            self.save()

    def save(self):
        from pathlib import Path
        if self.busy:
            return
        if not self.folder.text().strip():
            self.status.setText("저장 위치를 선택해 주세요.")
            return
        path = Path(self.folder.text().strip()).expanduser() / self.report.filename
        if path.exists():
            dialog = ImportConfirmDialog(f"같은 이름의 파일이 있습니다. 교체할까요?\n{path}", self,
                                         title="기존 보고서 파일 확인", action_text="교체")
            accepted = dialog.exec() == dialog.DialogCode.Accepted
            dialog.deleteLater()
            if not accepted:
                return
        self.busy = True
        for widget in (self.folder, self.browse_button, self.cancel_button, self.save_button, self.retry_button):
            widget.setEnabled(False)
        self.status.setText("보고서 파일 저장 중")
        self.progress.show()
        report, pictures = self.report, self.pictures
        self.request.start(BackgroundRead(lambda: save_generated_report(report, path, pictures)))

    def _finished(self, result, error):
        self.busy = False
        self.progress.hide()
        for widget in (self.folder, self.browse_button, self.cancel_button, self.save_button, self.retry_button):
            widget.setEnabled(True)
        if error is None and result.saved:
            self.saved.emit(result.path)
            self.accept()
        else:
            self.status.setText(str(error) if error else result.message)
            self.retry_button.show()

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        event.ignore() if self.busy else super().closeEvent(event)
