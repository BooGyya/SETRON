"""전체 앱의 테마 경계. 지역 QSS, 동적 표·직접 그리기와 새 팝업도 같은 토큰을 쓴다."""

import hashlib
from pathlib import Path
import re

from PySide6.QtCore import QEvent, QObject, QStandardPaths, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QToolTip, QWidget

from setron.ui.appearance_data import Appearance, PersonalSettings


def manager():
    app = QApplication.instance()
    return getattr(app, "appearance_manager", None) if app else None


def scaled_font(base, factor=None):
    """디자인의 원래 크기에만 배율을 적용해 반복 변경 시 크기가 누적되지 않게 한다."""
    if factor is None:
        controller = manager()
        factor = controller.appearance.font_scale if controller else 1.0
    result = QFont(base)
    if base.pixelSize() > 0:
        result.setPixelSize(max(1, round(base.pixelSize() * factor)))
    elif base.pointSizeF() > 0:
        result.setPointSizeF(base.pointSizeF() * factor)
    return result


def refresh_widget_font(widget):
    base = widget.property("setronBaseFont")
    if base is not None:
        value = scaled_font(base, widget.property("setronPreviewFontScale"))
        if widget.font() != value:
            widget.setFont(value)


def chart_font(part, name, base=None):
    """축·범례·막대 값의 원래 폰트를 보존하고 같은 배율로 그린다."""
    key = "setronBase_" + name
    getter = getattr(part, name)
    original = base if base is not None else part.property(key)
    if original is None:
        original = getter()
    part.setProperty(key, original)
    value = scaled_font(original)
    if getter() != value:
        getattr(part, "set" + name[0].upper() + name[1:])(value)


CHART_TEXT_BASE_FONT = 1


def chart_text_font(item, base):
    item.setData(CHART_TEXT_BASE_FONT, base)
    item.setFont(scaled_font(base))


def ui_color(*args):
    """Qt 원색 생성과 같은 시그니처. 그림자 RGBA·투명도는 보존한다."""
    current = manager()
    if len(args) == 1 and isinstance(args[0], str) and current:
        return QColor(current.color(args[0], "paint"))
    return QColor(*args)


def contrasting(value):
    color = QColor(value)
    def luminance(c):
        channels = (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
                    for v in (c.redF(), c.greenF(), c.blueF()))
        return sum(a * b for a, b in zip(channels, (.2126, .7152, .0722)))
    light, dark, bg = luminance(QColor("#f4f7ff")), luminance(QColor("#142033")), luminance(color)
    return "#142033" if (bg + .05) / (dark + .05) >= (light + .05) / (bg + .05) else "#f4f7ff"


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self, app, store=None):
        super().__init__(app)
        self.app, self.store = app, store or PersonalSettings()
        self.appearance = self.store.appearance()
        self.base_stylesheet = app.styleSheet()
        self.base_palette = QPalette(app.palette())
        self.base_font = QFont(app.font())
        self._guard = False
        self._asset_cache = {}
        app.installEventFilter(self)
        app.styleHints().colorSchemeChanged.connect(self._system_changed)

    @property
    def dark(self):
        return (self.appearance.mode == "dark" or self.appearance.mode == "system"
                and self.app.styleHints().colorScheme() == Qt.ColorScheme.Dark)

    @property
    def tokens(self):
        return self._tokens if hasattr(self, "_tokens") else self.appearance.colors(self.dark)

    def _system_changed(self, *_):
        if self.appearance.mode == "system":
            self.apply(self.appearance)

    def color(self, value, role="color"):
        original = QColor(value)
        if not original.isValid():
            return value
        key, t = original.name().lower(), self.tokens
        # 기본 라이트는 기존 UI의 파스텔 상태색을 한 글자도 치환하지 않는다.
        overrides = self.appearance.dark_colors if self.dark else vars(self.appearance)
        if key in ("#3c5ce9", "#3d5afe", "#4f46e5"):
            return (t["primary"] if role == "background" else t["accent"]) if self.dark or overrides.get("accent") else value
        if key in ("#3652ca", "#304dc9"):
            return (t["primary_hover"] if self.dark else QColor(t["accent"]).darker(115).name()) if self.dark or overrides.get("accent") else value
        if key in ("#ddf4e5", "#d4ebdf", "#e3f5ec", "#e4f5ed"):
            return t["met"] if self.dark or self.appearance.palette != "standard" else value
        if key in ("#fff0b7", "#fbe5a3", "#fff6d2", "#fdf3d6", "#fff5d6"):
            return t["below"] if self.dark or self.appearance.palette != "standard" else value
        if key in ("#ffe1e5", "#fce2e7", "#fcf0f2", "#fde6e9"):
            return t["risk"] if self.dark or self.appearance.palette != "standard" else value
        if key == "#f5f6f8":
            return t["canvas"]
        if key == "#ffffff":
            return (t["ink"] if self.dark else "#ffffff") if role == "foreground" else t["surface"]
        if not self.dark:
            return value
        if key in ("#142033", "#111827", "#000000", "#1f2937"):
            return t["ink"] if role != "background" else t["soft"]
        if key in ("#5b6880", "#66708a", "#64748b", "#6b7280"):
            return t["muted"]
        if key in ("#edf0fd", "#e8ebfc", "#e7ebff", "#d8defb", "#e8efff", "#dfe4fb"):
            return t["selected"]
        if key == "#a8b4fa":
            return t["accent"]
        if key in ("#f9fafb", "#f1f3f7", "#f0f3f8", "#f7f9fc", "#f8fafc"):
            return t["soft"]
        if key in ("#d5d7da", "#dee0e5", "#e5eaf3", "#e9eef6", "#e9ecf1", "#cbd3e1"):
            return t["line"]
        # 기타 화면의 기존 상태색도 색조를 보존한다. 업무 판정/원본 수치는 바꾸지 않는다.
        h, s, l, a = original.getHslF()
        if role in ("background", "border") or role == "paint" and l > .78:
            l = min(.28, max(.13, (1 - l) * .5 + .14))
        elif l < .65:
            l = max(.68, l)
        return QColor.fromHslF(h, s, l, a).name()

    def stylesheet(self, text):
        if not text:
            return text
        def declaration(match):
            name, value = match.group(1), match.group(2)
            if name == "font-size":
                value = re.sub(r"(\d+(?:\.\d+)?)(px|pt)\b",
                               lambda m: f"{float(m[1]) * self.appearance.font_scale:g}{m[2]}", value)
            role = "background" if "background" in name else "border" if "border" in name else "foreground"
            value = re.sub(r"#[0-9a-fA-F]{6}\b|\bwhite\b|\bblack\b", lambda m: self.color(m[0], role), value)
            return name + ":" + value
        result = re.sub(r"([\w-]+)\s*:([^;{}]+)", declaration, text)
        # 흰 글자는 기본적으로 흰색을 보존한다. 진한/밝은 primary 배경인 규칙만
        # 그 배경의 실제 명도로 on-primary를 결정한다. 전역 글자색을 바꾸지 않는다.
        def primary_rule(match):
            selector, original = match.group(1), match.group(2)
            rendered = re.sub(r"([\w-]+)\s*:([^;{}]+)", declaration, original)
            if re.search(r"background[^;]*#[34][0-9a-fA-F]{5}", original) and re.search(r"color\s*:\s*(white|#ffffff)\b", original, re.I):
                rendered = re.sub(r"(?<![\w-])color\s*:[^;{}]+", "color:" + contrasting(self.tokens["primary"]), rendered)
            return selector + "{" + rendered + "}"
        if "{" in text:
            result = re.sub(r"([^{}]+)\{([^{}]*)\}", primary_rule, text)
        if self.dark:
            result = re.sub(r"url\(([^)]+\.svg)\)", lambda m: "url(" + self._asset_path(m[1]) + ")", result)
        return result

    def _asset_path(self, source):
        source = source.strip("\"'")
        if Path(source).name == "checkbox-tick.svg":
            return source
        key = source, self.tokens["ink"]
        if key not in self._asset_cache:
            from PySide6.QtSvg import QSvgRenderer
            size = QSvgRenderer(source).defaultSize()
            if size.isEmpty():
                return source
            path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "theme-icons"
            path.mkdir(parents=True, exist_ok=True)
            target = path / (hashlib.sha256(repr(key).encode()).hexdigest()[:20] + ".png")
            pixmap = self.themed_icon(QIcon(source), size).pixmap(size)
            if not target.exists() and not pixmap.save(str(target)):
                return source
            self._asset_cache[key] = target.as_posix()
        return self._asset_cache[key]

    def _widget(self, widget):
        refresh_widget_font(widget)
        if widget.property("themePreview"):
            return
        if widget.property("setronOwnPalette"):
            widget.setPalette(self.app.palette())
        original = widget.property("setronOriginalStyle")
        rendered = widget.property("setronRenderedStyle")
        current = widget.styleSheet()
        if original is None or current != rendered:
            original = current
            widget.setProperty("setronOriginalStyle", original)
        target = self.stylesheet(original)
        if widget.property("appearanceRate") == "foreground":
            target = "color:" + self.tokens["rate"] + ";"
        elif widget.property("appearanceRate") == "background":
            # 강조색은 숫자/테두리에만 사용하고, 넓은 카드 면은 부드러운 톤으로 둔다.
            target = (f"QFrame#card {{background:{self.tokens['selected']};"
                      f"border:1px solid {self.tokens['line']};border-radius:12px;}}")
        if widget.parentWidget() and widget.parentWidget().property("appearanceRate") == "background":
            if callable(getattr(widget, "text", None)):
                target = "color:" + self.tokens["rate" if widget.property("rateValue") else "muted"] + ";"
        if current != target:
            widget.setProperty("setronRenderedStyle", target)
            widget.setStyleSheet(target)
        else:
            # 지금 적용된 스타일을 늘 기록한다. 옛 값이 남으면 A→B→A로 바꿀 때 되돌린 A를 바뀌지 않은 것으로 보고
            # B를 다시 입힌다 (가져오기 'AI 추출 전' 배지가 노랗게 남던 문제, 11-10).
            widget.setProperty("setronRenderedStyle", target)
        # 아이콘은 원본 자산과 슬롯 크기를 유지하고 다크 모드에서만 명암을 조정한다.
        asset = widget.property("assetFile")
        if asset and hasattr(widget, "setPixmap") and "ff5f9" not in asset:
            from setron.ui.theme import ASSETS, svg_size
            pixmap = QIcon(str(ASSETS / asset)).pixmap(svg_size(asset))
            if self.dark:
                painter = QPainter(pixmap)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), QColor(self.tokens["accent"] if "active-" in asset else self.tokens["ink"]))
                painter.end()
            if hasattr(widget, "set_arrow_pixmap"):
                widget.set_arrow_pixmap(pixmap)
            else:
                widget.setPixmap(pixmap)
        if callable(getattr(widget, "icon", None)) and hasattr(widget, "setIcon") and not widget.property("fixedColorIcon"):
            original_icon = widget.property("setronOriginalIcon")
            if original_icon is None and not widget.icon().isNull():
                original_icon = widget.icon()
                widget.setProperty("setronOriginalIcon", original_icon)
            if original_icon is not None:
                widget.setIcon(self.themed_icon(original_icon, widget.iconSize()))
        for action in QWidget.actions(widget):
            original_icon = action.property("setronOriginalIcon")
            if original_icon is None and not action.icon().isNull():
                original_icon = action.icon()
                action.setProperty("setronOriginalIcon", original_icon)
            if original_icon is not None:
                from PySide6.QtCore import QSize
                action.setIcon(self.themed_icon(original_icon, QSize(18, 18)))
        widget.update()

    def themed_icon(self, original, size, color=None):
        if not self.dark:
            return original
        pixmap = original.pixmap(size)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color or self.tokens["ink"]))
        painter.end()
        return QIcon(pixmap)

    def eventFilter(self, obj, event):
        if not self._guard and isinstance(obj, QWidget) and event.type() in (
                QEvent.Type.Polish, QEvent.Type.Show, QEvent.Type.StyleChange):
            self._guard = True
            try:
                self._widget(obj)
                if event.type() == QEvent.Type.Show:
                    self._chart(obj)
            finally:
                self._guard = False
        return False

    def _chart(self, widget):
        from PySide6.QtCharts import QChartView
        if not isinstance(widget, QChartView):
            return
        from PySide6.QtGui import QBrush, QPen
        from PySide6.QtWidgets import QGraphicsSimpleTextItem
        chart, t = widget.chart(), self.tokens
        chart.setBackgroundBrush(QBrush(QColor(t["surface"])))
        chart.setPlotAreaBackgroundBrush(QBrush(QColor(t["surface"])))
        chart.setTitleBrush(QBrush(QColor(t["ink"])))
        chart_font(chart, "titleFont")
        chart_font(chart.legend(), "font")
        chart.legend().setLabelColor(QColor(t["ink"]))
        widget.setBackgroundBrush(QBrush(QColor(t["surface"])))
        for axis in chart.axes():
            chart_font(axis, "labelsFont")
            chart_font(axis, "titleFont")
            axis.setLabelsColor(QColor(t["muted"]))
            axis.setTitleBrush(QBrush(QColor(t["ink"])))
            axis.setLinePen(QPen(QColor(t["line"])))
            axis.setGridLinePen(QPen(QColor(t["line"])))
        for item in chart.scene().items() if chart.scene() else ():
            if isinstance(item, QGraphicsSimpleTextItem):
                item.setBrush(QBrush(QColor(t["ink"])))
                if (base := item.data(CHART_TEXT_BASE_FONT)) is not None:
                    item.setFont(scaled_font(base))
        if place_labels := getattr(chart, "_setron_place_labels", None):
            place_labels(chart.plotArea())
        for series in chart.series():
            if hasattr(series, "barSets"):
                for bar_set in series.barSets():
                    chart_font(bar_set, "labelFont")
            if hasattr(series, "color") and hasattr(series, "setColor"):
                original = series.property("setronOriginalColor")
                if original is None:
                    original = series.color().name()
                    series.setProperty("setronOriginalColor", original)
                series.setColor(QColor(self.color(original, "foreground")))

    def _begin(self, appearance):
        self.appearance = appearance
        self._tokens = appearance.colors(self.dark)
        self.app.setFont(scaled_font(self.base_font, appearance.font_scale))
        t = self.tokens
        palette = QPalette(self.base_palette)
        for role, color in ((QPalette.ColorRole.Window, t["canvas"]), (QPalette.ColorRole.Base, t["surface"]),
                            (QPalette.ColorRole.AlternateBase, t["soft"]), (QPalette.ColorRole.Button, t["soft"]),
                            (QPalette.ColorRole.WindowText, t["ink"]), (QPalette.ColorRole.Text, t["ink"]),
                            (QPalette.ColorRole.ButtonText, t["ink"]), (QPalette.ColorRole.Highlight, t["selected"]),
                            (QPalette.ColorRole.HighlightedText, t["accent"]), (QPalette.ColorRole.PlaceholderText, t["muted"]),
                            (QPalette.ColorRole.ToolTipBase, t["soft"]), (QPalette.ColorRole.ToolTipText, t["ink"])):
            palette.setColor(role, QColor(color))
        if self.dark:
            for role in (QPalette.ColorRole.Light, QPalette.ColorRole.Midlight, QPalette.ColorRole.Mid, QPalette.ColorRole.Dark):
                palette.setColor(role, QColor(t["line"]))
        self.app.setPalette(palette)
        QToolTip.setPalette(palette)
        extra = ("QLineEdit,QTextEdit,QPlainTextEdit,QComboBox,QSpinBox,QDateEdit {background:#f9fafb;}"
                 "QTableView,QTableWidget,QAbstractItemView {background:white;alternate-background-color:#f9fafb;"
                 "selection-background-color:#edf0fd;selection-color:#142033;}"
                 "QHeaderView::section {background:#f9fafb;color:#5b6880;}"
                 "QMenu,QDialog,QCalendarWidget {background:white;}"
                 "QTabWidget::pane {border:0;} QStackedWidget {background:transparent;}")
        self.app.setStyleSheet(self.stylesheet(self.base_stylesheet + extra))

    def apply(self, appearance, *, persist=False):
        if persist:
            self.store.save("appearance", appearance)
        self._guard = True
        try:
            self._begin(appearance)
            for widget in QApplication.allWidgets():
                self._widget(widget)
                self._chart(widget)
        finally:
            self._guard = False
        self.changed.emit()

    def apply_async(self, appearance, finished):
        """GUI 변경만 메인 스레드에서 나누어 처리해 진행 표시가 계속 그려지게 한다."""
        from shiboken6 import isValid
        self._guard = True
        try:
            self._begin(appearance)
        finally:
            self._guard = False
        widgets = list(QApplication.allWidgets())
        def batch():
            self._guard = True
            try:
                for _ in range(min(24, len(widgets))):
                    widget = widgets.pop()
                    if isValid(widget):
                        self._widget(widget)
                        self._chart(widget)
            finally:
                self._guard = False
            if widgets:
                QTimer.singleShot(0, batch)
            else:
                self.changed.emit()
                finished()
        QTimer.singleShot(0, batch)


def install_appearance(app, store=None):
    if manager() is None:
        app.appearance_manager = ThemeManager(app, store)
        app.appearance_manager.apply(app.appearance_manager.appearance)
    return app.appearance_manager
