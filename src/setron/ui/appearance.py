"""전체 앱의 테마 경계. 지역 QSS, 동적 표·직접 그리기와 새 팝업도 같은 토큰을 쓴다."""

import hashlib
from functools import lru_cache
from pathlib import Path
import re

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QStandardPaths, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QDialog, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QTabBar, QTextEdit, QToolTip, QWidget,
)
from shiboken6 import isValid

from setron.ui.appearance_data import Appearance, PersonalSettings


_EXTRA_STYLE = ("QLineEdit,QTextEdit,QPlainTextEdit,QComboBox,QSpinBox,QDateEdit {background:#f9fafb;color:#142033;}"
                "QTableView,QTableWidget,QAbstractItemView {background:white;alternate-background-color:palette(alternate-base);"
                "color:palette(text);"
                "selection-background-color:#edf0fd;selection-color:#142033;}"
                "QHeaderView::section {background:#f9fafb;color:#5b6880;}"
                "QMenu,QDialog,QCalendarWidget {background:white;}"
                "QTabWidget::pane {border:0;} QStackedWidget {background:transparent;}")

_COLOR_VALUE = r"(#[0-9a-fA-F]{6}\b|\bwhite\b|\bblack\b)"
_BACKGROUND = re.compile(r"(?<![\w-])background(?:-color)?\s*:\s*" + _COLOR_VALUE + r"\s*(?=;|$)")
_TEXT_COLOR = re.compile(r"(?<![\w-])color\s*:\s*" + _COLOR_VALUE)


@lru_cache(maxsize=512)
def _solid_background_rules(style):
    """위젯 자체의 단색 배경만 찾는다. 하위 요소나 hover 규칙은 제외한다."""
    if "{" not in style:
        match = _BACKGROUND.search(style)
        return ((None, None, match[1]),) if match else ()
    result = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style):
        if background := _BACKGROUND.search(body.strip()):
            for selector in selectors.split(","):
                match = re.fullmatch(r"(\w+)(?:#([\w-]+))?", selector.strip())
                if match:
                    result.append((*match.groups(), background[1]))
    return tuple(result)


def _solid_background(widget, style):
    for class_name, name, color in reversed(_solid_background_rules(style)):
        if (name is None or name == widget.objectName()) and (class_name is None or widget.inherits(class_name)):
            return color
    return None


def _readable_text_style(style, background):
    def declarations(body):
        solid = _BACKGROUND.search(body)
        fill = solid[1] if solid else background
        return _TEXT_COLOR.sub(lambda m: "color:" + readable_color(m[1], fill), body)

    if "{" not in style:
        return declarations(style)
    return re.sub(r"\{([^{}]*)\}", lambda m: "{" + declarations(m[1]) + "}", style)


def _render_stylesheet(text, color, font_scale, primary_ink):
    def declaration(match):
        name, value = match.group(1), match.group(2)
        if name == "font-size":
            value = re.sub(r"(\d+(?:\.\d+)?)(px|pt)\b",
                           lambda m: f"{float(m[1]) * font_scale:g}{m[2]}", value)
        role = "background" if "background" in name else "border" if "border" in name else "foreground"
        value = re.sub(r"#[0-9a-fA-F]{6}\b|\bwhite\b|\bblack\b", lambda m: color(m[0], role), value)
        return name + ":" + value

    def render_declarations(body):
        return re.sub(r"([\w-]+)\s*:([^;{}]+)", declaration, body)

    def rule(match):
        selector, body = match.groups()
        rendered = render_declarations(body)
        if re.search(r"background[^;]*#[34][0-9a-fA-F]{5}", body) and re.search(r"color\s*:\s*(white|#ffffff)\b", body, re.I):
            rendered = re.sub(r"(?<![\w-])color\s*:[^;{}]+", "color:" + primary_ink, rendered)
        return selector + "{" + rendered + "}"

    return re.sub(r"([^{}]+)\{([^{}]*)\}", rule, text) if "{" in text else render_declarations(text)


def _dark_selector(selector):
    # 전역 스타일의 각 선택자가 가리키는 위젯에만 모드 속성을 붙인다.
    ancestor, space, last = selector.strip().rpartition(" ")
    target, colon, pseudo = last.partition(":")
    return f'{ancestor}{space}{target}[setronDark="true"]{colon}{pseudo}'


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


@lru_cache(maxsize=256)
def _luminance(value):
    color = QColor(value)
    channels = (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
                for v in (color.redF(), color.greenF(), color.blueF()))
    return sum(a * b for a, b in zip(channels, (.2126, .7152, .0722)))


def contrast_ratio(foreground, background):
    low, high = sorted((_luminance(foreground), _luminance(background)))
    return (high + .05) / (low + .05)


def contrasting(background):
    return max(("#142033", "#f4f7ff"), key=lambda color: contrast_ratio(color, background))


@lru_cache(maxsize=256)
def readable_color(foreground, background, minimum=4.5):
    """배경과의 대비가 부족할 때만 원래 글자색을 밝히거나 어둡게 한다."""
    if contrast_ratio(foreground, background) >= minimum:
        return foreground
    target = max(("#000000", "#ffffff"), key=lambda color: contrast_ratio(color, background))
    original = QColor(foreground).getRgbF()[:3]
    endpoint = QColor(target).redF()
    for step in range(1, 10):
        mix = step / 10
        color = QColor.fromRgbF(*(value * (1 - mix) + endpoint * mix for value in original)).name()
        if contrast_ratio(color, background) >= minimum:
            return color
    return target


def readable_ink(foreground, background):
    return QColor(readable_color(foreground.name(), background.name()))


def surface_edge(fill, surface, line):
    """채움색이 주변과 거의 같으면 경계선을 더 뚜렷하게 표시한다."""
    return readable_color(line, surface, 3) if contrast_ratio(fill, surface) < 1.15 else line


def set_color_style(widget, render):
    """상태에 해당하는 토큰으로 그리며 일반 QSS의 색 치환을 거치지 않는다."""
    widget._setron_color_style = render
    controller = manager()
    style = render(controller.tokens if controller else Appearance().colors())
    widget.setProperty("setronRenderedStyle", style)
    if widget.styleSheet() != style:
        widget.setStyleSheet(style)


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
        self._applying_async = False
        self._applied_dark = self.dark
        self._global_colors = None
        self._global_revision = 0
        self._global_targets = ()
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
        if self.appearance.mode == "system" and not self._applying_async:
            self.apply(self.appearance)

    def color(self, value, role="color"):
        dark = self.dark
        custom_accent = self.appearance.dark_colors.get("accent") if dark else self.appearance.accent
        return self._theme_color(value, role, self.tokens, dark, custom_accent)

    @staticmethod
    def _theme_color(value, role, t, dark, custom_accent=None):
        original = QColor(value)
        if not original.isValid():
            return value
        key = original.name().lower()
        # 기본 라이트는 기존 UI의 파스텔 상태색을 한 글자도 치환하지 않는다.
        if key in ("#3c5ce9", "#3d5afe", "#4f46e5"):
            return (t["primary"] if role == "background" else t["accent"]) if dark or custom_accent else value
        if key in ("#3652ca", "#304dc9"):
            return (t["primary_hover"] if dark else QColor(t["accent"]).darker(115).name()) if dark or custom_accent else value
        if key in ("#ddf4e5", "#d4ebdf", "#e3f5ec", "#e4f5ed"):
            return t["status_met"] if dark else value
        if key in ("#fff0b7", "#fbe5a3", "#fff6d2", "#fdf3d6", "#fff5d6"):
            return t["status_below"] if dark else value
        if key in ("#ffe1e5", "#fce2e7", "#fcf0f2", "#fde6e9"):
            return t["status_risk"] if dark else value
        if key == "#f5f6f8":
            return t["canvas"]
        if key == "#ffffff":
            return (t["ink"] if dark else "#ffffff") if role == "foreground" else t["surface"]
        if not dark:
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
        primary = self.tokens["primary"]
        result = _render_stylesheet(text, self.color, self.appearance.font_scale,
                                    readable_color(contrasting(primary), primary))
        if self.dark:
            result = re.sub(r"url\(([^)]+\.svg)\)", lambda m: "url(" + self._asset_path(m[1]) + ")", result)
        return result

    def install_global_stylesheet(self):
        """전역 QSS는 한 번만 등록한다. 모드와 팔레트는 개별 위젯에 나누어 적용한다."""
        source = self.base_stylesheet + _EXTRA_STYLE

        def rules(dark):
            tokens = Appearance().colors(dark)

            def color(value, role):
                key = value.lower()
                if key in ("white", "#ffffff") and role != "foreground":
                    return "palette(base)"
                roles = {"#f5f6f8": "window", "#3c5ce9": "link-visited" if role == "background" else "link",
                         "#3652ca": "midlight", "#304dc9": "shadow"}
                if dark and key == "#a8b4fa":
                    return "palette(link)"
                return f"palette({roles[key]})" if key in roles else self._theme_color(value, role, tokens, dark)

            rendered = _render_stylesheet(source, color, 1, "palette(bright-text)")
            return re.findall(r"([^{}]+)\{([^{}]*)\}", rendered)

        light_rules, dark_rules = rules(False), rules(True)
        targets = set()
        for (selectors, light), (_, dark) in zip(light_rules, dark_rules):
            if light == dark and "palette(" not in light:
                continue
            for selector in selectors.split(","):
                match = re.match(r"(\w+)(?:#([\w-]+))?", selector.strip().rsplit(None, 1)[-1])
                if match:
                    class_name, object_name = match.groups()
                    targets.add(("QTipLabel" if class_name == "QToolTip" else class_name, object_name))
        self._global_targets = tuple(targets)
        # 선언 순서는 원본과 같게 유지한다. 포커스·비활성 규칙의 우선순위도 함께 보존한다.
        scoped = [",".join(map(_dark_selector, selectors.split(","))) + "{" + body + "}"
                  for selectors, body in dark_rules]
        self.app.setStyleSheet("\n".join([*(selector + "{" + body + "}" for selector, body in light_rules), *scoped]))

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
        repolish = False
        if widget.property("setronGlobalRevision") != self._global_revision:
            widget.setProperty("setronGlobalRevision", self._global_revision)
            name = widget.objectName()
            if any((object_name is None or name == object_name) and widget.inherits(class_name)
                   for class_name, object_name in self._global_targets):
                widget.setProperty("setronDark", self._applied_dark)
                repolish = widget.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)
        refresh_widget_font(widget)
        if widget.property("themePreview"):
            if repolish:
                self._repolish(widget)
            return
        if widget.property("setronOwnPalette"):
            widget.setPalette(self.app.palette())
        original = widget.property("setronOriginalStyle")
        rendered = widget.property("setronRenderedStyle")
        current = widget.styleSheet()
        if original is None or current != rendered:
            original = current
            widget.setProperty("setronOriginalStyle", original)
        render = getattr(widget, "_setron_color_style", None)
        target = render(self.tokens) if render else self.stylesheet(original)
        if widget.property("appearanceRate") == "foreground":
            target = "color:" + readable_color(self.tokens["rate"], self.tokens["surface"]) + ";"
        elif widget.property("appearanceRate") == "background":
            # 강조색은 숫자/테두리에만 사용하고, 넓은 카드 면은 부드러운 톤으로 둔다.
            target = (f"QFrame#card {{background:{self.tokens['selected']};"
                      f"border:1px solid {self.tokens['line']};border-radius:12px;}}")
        if widget.parentWidget() and widget.parentWidget().property("appearanceRate") == "background":
            if callable(getattr(widget, "text", None)):
                key = (widget.parentWidget().property("appearanceRateKey") or "rate") if widget.property("rateValue") else "muted"
                target = "color:" + readable_color(self.tokens[key], self.tokens["selected"]) + ";"
        text_background = self._text_background(widget, target) if isinstance(widget, (QLabel, QTabBar)) else None
        if text_background and target:
            target = _readable_text_style(target, text_background)
        if current != target:
            widget.setProperty("setronRenderedStyle", target)
            widget.setStyleSheet(target)
        else:
            # 지금 적용된 스타일을 늘 기록한다. 옛 값이 남으면 A→B→A로 바꿀 때 되돌린 A를 바뀌지 않은 것으로 보고
            # B를 다시 입힌다 (가져오기 'AI 추출 전' 배지가 노랗게 남던 문제, 11-10).
            widget.setProperty("setronRenderedStyle", target)
            if repolish:
                self._repolish(widget)
        if text_background and isinstance(widget, QLabel) and not _TEXT_COLOR.search(target):
            # 스타일 없는 QLabel도 부모의 예전 팔레트를 상속하지 않도록 글자 역할을 명시한다.
            role = widget.foregroundRole()
            ink = QColor(readable_color(self.tokens["ink"], text_background))
            palette = widget.palette()
            if palette.color(role) != ink:
                palette.setColor(role, ink)
                widget.setPalette(palette)
        self._refresh_input_font(widget, target)
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
        getter = getattr(widget, "icon", None)
        current_icon = (getter() if callable(getter) and hasattr(widget, "setIcon")
                        and not widget.property("fixedColorIcon") else None)
        # QMessageBox.icon()은 그림(QIcon)이 아니라 종류(물음표·경고)를 돌려준다. 그림일 때만 테마 색을 입힌다.
        # (11/11: 확인창이 뜰 때마다 'Icon' object has no attribute 'isNull' 오류가 이어져 확인창이 답을 못 돌려주던 문제)
        if isinstance(current_icon, QIcon):
            original_icon = widget.property("setronOriginalIcon")
            if original_icon is None and not current_icon.isNull():
                original_icon = current_icon
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

    def _refresh_input_font(self, widget, style):
        """일부 입력 위젯은 고정 QSS에서 이전 기본 글꼴을 보관한다."""
        if (not isinstance(widget, (QLineEdit, QAbstractSpinBox, QComboBox, QTextEdit, QPlainTextEdit))
                or widget.property("setronBaseFont") is not None
                or re.search(r"(?<![\w-])font(?:-size)?\s*:", style)):
            return
        if widget.testAttribute(Qt.WidgetAttribute.WA_SetFont) and not widget.property("setronInputFont"):
            return
        parent = widget.parentWidget()
        font = parent.font() if parent is not None and parent.property("setronBaseFont") is not None else self.app.font()
        if widget.font() != font:
            widget.setProperty("setronInputFont", True)
            widget.setFont(font)

    def _text_background(self, widget, style):
        if background := _solid_background(widget, style):
            return background
        colors = self.tokens
        owner = widget.parentWidget()
        while owner is not None:
            if owner.property("appearanceRate") == "background":
                return colors["selected"]
            if isinstance(owner, QPushButton):
                if owner.property("nav") and owner.isChecked():
                    return colors["selected"]
                if owner.property("settingsOption"):
                    return colors["selected" if owner.isChecked() else "soft"]
            current = owner.styleSheet()
            render = getattr(owner, "_setron_color_style", None)
            if render:
                if background := _solid_background(owner, render(colors)):
                    return background
            elif current:
                original = owner.property("setronOriginalStyle") if current == owner.property("setronRenderedStyle") else current
                if background := _solid_background(owner, original or ""):
                    return self.color(background, "background")
            name = owner.objectName()
            if name == "canvas":
                return owner.property("setronCanvasPreview") or colors["canvas"]
            if name in ("card", "sidebar", "sidebarContent", "dashboardMatrix") or isinstance(owner, QDialog):
                return colors["surface"]
            owner = owner.parentWidget()
        return colors["canvas"]

    def refresh_canvas_preview(self, canvas):
        guard, self._guard = self._guard, True
        try:
            for widget in (*canvas.findChildren(QLabel), *canvas.findChildren(QTabBar)):
                if widget.isVisibleTo(canvas):
                    self._widget(widget)
        finally:
            self._guard = guard

    @staticmethod
    def _repolish(widget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

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
        from PySide6.QtCharts import QChartView, QPieSeries
        if not isinstance(widget, QChartView):
            return
        from PySide6.QtGui import QBrush, QPen
        from PySide6.QtWidgets import QGraphicsSimpleTextItem
        chart, t = widget.chart(), self.tokens
        ink = QColor(readable_color(t["ink"], t["surface"]))
        muted = QColor(readable_color(t["muted"], t["surface"]))
        chart.setBackgroundBrush(QBrush(QColor(t["surface"])))
        chart.setPlotAreaBackgroundBrush(QBrush(QColor(t["surface"])))
        chart.setTitleBrush(QBrush(ink))
        chart_font(chart, "titleFont")
        chart_font(chart.legend(), "font")
        chart.legend().setLabelColor(ink)
        widget.setBackgroundBrush(QBrush(QColor(t["surface"])))
        for axis in chart.axes():
            chart_font(axis, "labelsFont")
            chart_font(axis, "titleFont")
            axis.setLabelsColor(muted)
            axis.setTitleBrush(QBrush(ink))
            axis.setLinePen(QPen(QColor(t["line"])))
            axis.setGridLinePen(QPen(QColor(t["line"])))
        for item in chart.scene().items() if chart.scene() else ():
            if isinstance(item, QGraphicsSimpleTextItem):
                item.setBrush(QBrush(ink))
                if (base := item.data(CHART_TEXT_BASE_FONT)) is not None:
                    item.setFont(scaled_font(base))
        if place_labels := getattr(chart, "_setron_place_labels", None):
            place_labels(chart.plotArea())
        for series in chart.series():
            if isinstance(series, QPieSeries):
                for part in series.slices():
                    if key := part.property("dashboardCompositionColor"):
                        part.setColor(QColor(t[key]))
                        part.setBorderColor(QColor(surface_edge(t[key], t["surface"], t[key])))
            if hasattr(series, "barSets"):
                for bar_set in series.barSets():
                    chart_font(bar_set, "labelFont")
            if hasattr(series, "color") and hasattr(series, "setColor"):
                original = series.property("setronOriginalColor")
                if original is None:
                    original = series.color().name()
                    series.setProperty("setronOriginalColor", original)
                series.setColor(QColor(self.color(original, "foreground")))

    def _update_steps(self, appearance):
        self.appearance = appearance
        self._applied_dark = self.dark
        self._tokens = appearance.colors(self._applied_dark)
        t = self.tokens
        primary_ink = readable_color(contrasting(t["primary"]), t["primary"])
        gradient_end = readable_color(self.color("#3652ca", "background"), primary_ink)
        hover = readable_color(self.color("#304dc9", "background"), primary_ink)
        shared_colors = (self._applied_dark, t["canvas"], t["surface"], t["accent"], t["primary"],
                         gradient_end, hover)
        if self._global_colors != shared_colors:
            self._global_colors = shared_colors
            self._global_revision += 1
        font = scaled_font(self.base_font, appearance.font_scale)
        if self.app.font() != font:
            self.app.setFont(font)
            yield
        if QToolTip.font() != font:
            QToolTip.setFont(font)
        palette = QPalette(self.base_palette)
        surface_ink = readable_color(t["ink"], t["surface"])
        alternate = t["soft"] if contrast_ratio(surface_ink, t["soft"]) >= 4.5 else t["surface"]
        link_ink = readable_color(t["accent"], t["selected"])
        for role, color in ((QPalette.ColorRole.Window, t["canvas"]), (QPalette.ColorRole.Base, t["surface"]),
                            (QPalette.ColorRole.AlternateBase, alternate), (QPalette.ColorRole.Button, t["soft"]),
                            (QPalette.ColorRole.WindowText, readable_color(t["ink"], t["canvas"])),
                            (QPalette.ColorRole.Text, surface_ink),
                            (QPalette.ColorRole.ButtonText, t["ink"]), (QPalette.ColorRole.Highlight, t["selected"]),
                            (QPalette.ColorRole.HighlightedText, link_ink), (QPalette.ColorRole.PlaceholderText, t["muted"]),
                            (QPalette.ColorRole.Link, link_ink), (QPalette.ColorRole.LinkVisited, t["primary"]),
                            (QPalette.ColorRole.BrightText, primary_ink),
                            (QPalette.ColorRole.Midlight, gradient_end), (QPalette.ColorRole.Shadow, hover),
                            (QPalette.ColorRole.ToolTipBase, t["soft"]), (QPalette.ColorRole.ToolTipText, t["ink"])):
            palette.setColor(role, QColor(color))
        if self._applied_dark:
            for role in (QPalette.ColorRole.Light, QPalette.ColorRole.Mid, QPalette.ColorRole.Dark):
                palette.setColor(role, QColor(t["line"]))
        if self.app.palette() != palette:
            self.app.setPalette(palette)
            QToolTip.setPalette(palette)
            yield
        for widget in self.app.allWidgets():
            if isValid(widget):
                self._widget(widget)
                self._chart(widget)
                yield

    def _hold_window_updates(self, except_window=None):
        windows = [w for w in self.app.topLevelWidgets()
                   if w is not except_window and w.isVisible() and w.updatesEnabled()]
        for window in windows:
            window.setUpdatesEnabled(False)
        return windows

    @staticmethod
    def _restore_window_updates(windows):
        for window in windows:
            if isValid(window):
                window.setUpdatesEnabled(True)

    def apply(self, appearance, *, persist=False):
        if persist:
            self.store.save("appearance", appearance)
        windows = self._hold_window_updates()
        try:
            self._guard = True
            try:
                for _ in self._update_steps(appearance):
                    pass
            finally:
                self._guard = False
            self.changed.emit()
        finally:
            self._restore_window_updates(windows)

    def apply_async(self, appearance, finished, *, progress_window):
        """본문은 완료 시 함께 그리되, 진행 팝업은 적용 중에도 갱신한다."""
        windows = self._hold_window_updates(except_window=progress_window)
        self._applying_async = True
        updates = self._update_steps(appearance)

        def apply_frame():
            nonlocal updates
            timer = QElapsedTimer()
            timer.start()
            self._guard = True
            try:
                while timer.elapsed() < 10:
                    next(updates)
            except StopIteration:
                if self.dark != self._applied_dark:
                    updates = self._update_steps(appearance)
                else:
                    self._guard = self._applying_async = False
                    try:
                        self.changed.emit()
                        finished()
                    finally:
                        self._restore_window_updates(windows)
                    return
            except Exception:
                self._applying_async = False
                self._restore_window_updates(windows)
                raise
            finally:
                self._guard = False
            QTimer.singleShot(1, self, apply_frame)

        QTimer.singleShot(16, self, apply_frame)


def install_appearance(app, store=None):
    if manager() is None:
        app.appearance_manager = ThemeManager(app, store)
        app.appearance_manager.install_global_stylesheet()
        app.appearance_manager.apply(app.appearance_manager.appearance)
    return app.appearance_manager
