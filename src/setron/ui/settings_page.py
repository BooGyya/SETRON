"""VER5 10 설정. 개인 설정은 로컬 저장, 업무 원본과 판정은 보존한다."""

from dataclasses import replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QVBoxLayout, QWidget,
)

from setron.ui.appearance import manager, readable_color, refresh_widget_font
from setron.ui.appearance_data import (
    ACCENTS, DASHBOARD_FIELDS, DASHBOARD_KEYS, FONT_SCALES, HEATMAP_FIELDS, HEATMAP_KEYS, Appearance, PersonalSettings,
)
from setron.ui.components import button, label
from setron.ui.dashboard import DashboardGrid, SplitRow
from setron.ui.import_widgets import Toast
from setron.ui.settings_color_dialog import SettingsColorDialog
from setron.ui.settings_preview import AppearancePreview, PreviewColumn
from setron.ui.settings_tasks import SettingsSave
from setron.ui.theme import SECONDARY
from setron.ui.version_management_widgets import actions, card, note, tabs, text_field


def _option(text, *, asset=None):
    choice = button(text, asset=asset)
    choice.setCheckable(True)
    choice.setProperty("settingsOption", True)
    return choice


class SettingsPage(QWidget):
    def __init__(self, *, store=None, controller=None, scroll_area=None, preview_background=None):
        super().__init__()
        self.controller = controller or manager()
        self.store = store or (self.controller.store if self.controller else PersonalSettings())
        self.saved = self.controller.appearance if self.controller else self.store.appearance()
        self.draft = self.saved
        self.scroll_area = scroll_area
        self._set_preview_background = preview_background
        self._canvas_preview_active = False
        self._rendering = False
        self.operation = SettingsSave(self)
        self.toast = None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tabs = tabs(("화면 · 테마",), "설정 화면 선택")
        self.tabs.setUsesScrollButtons(False)
        body.addWidget(self.tabs)
        self.appearance_page = self._appearance()
        body.addWidget(self.appearance_page)
        self.operation.changed.connect(lambda: self.appearance_page.setEnabled(not self.operation.busy))
        if self.controller:
            self.controller.changed.connect(self._sync_applied_font_size)
        self._render()

    def _appearance(self):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        controls = QWidget()
        settings = QVBoxLayout(controls)
        settings.setContentsMargins(0, 0, 0, 0)
        settings.setSpacing(20)
        display = card("표시 모드")
        self.mode_buttons = {}
        for key, title in (("light", "밝게"), ("dark", "어둡게"), ("system", "시스템")):
            b = _option(title)
            b.setMinimumHeight(70)
            b.setStyleSheet("QPushButton {min-height:88px;max-height:88px;padding:0;border-radius:12px;}"
                            "QPushButton:checked {background:#e8ebfc;border:1px solid #3c5ce9;}")
            b.clicked.connect(lambda checked=False, k=key: self.update_draft(mode=k))
            miniature = QWidget()
            miniature.setFixedSize(56, 40)
            miniature.setStyleSheet("background:white;border-radius:4px;")
            mini = QHBoxLayout(miniature)
            mini.setContentsMargins(5, 5, 5, 5)
            stripe = QFrame()
            stripe.setFixedWidth(8)
            stripe.setStyleSheet("background:#3c5ce9;")
            content = QFrame()
            content.setStyleSheet("background:#f5f6f8;border-top:5px solid #64748b;")
            mini.addWidget(stripe)
            mini.addWidget(content)
            box = QHBoxLayout(b)
            box.setContentsMargins(12, 8, 12, 8)
            b.setText("")
            b.setAccessibleName(title)
            box.addWidget(miniature)
            box.addWidget(label(title))
            for child in b.findChildren(QWidget):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.mode_buttons[key] = b
        display.body.addWidget(DashboardGrid(list(self.mode_buttons.values()), minimum_width=170, maximum_columns=3, spacing=8))
        settings.addWidget(display)
        sizing = card("글자 크기")
        sizing.body.addWidget(note("저장하면 앱의 모든 화면에 적용됩니다."))
        self.font_size_buttons = {}
        for key, title in (("small", "작게"), ("normal", "보통"), ("large", "크게")):
            choice = _option(title)
            # 선택된 전체 글자 크기와 별개로 각 버튼은 해당 크기를 보여 준다.
            choice.setProperty("setronPreviewFontScale", FONT_SCALES[key])
            refresh_widget_font(choice)
            choice.setAccessibleName(f"글자 크기 · {title}")
            choice.clicked.connect(lambda checked=False, k=key: self.update_draft(font_size=k))
            self.font_size_buttons[key] = choice
        sizing.body.addWidget(DashboardGrid(list(self.font_size_buttons.values()),
                                            minimum_width=110, maximum_columns=3, spacing=8))
        settings.addWidget(sizing)
        accent = card("포인트 색상")
        self.fields = {}
        self.color_picker_buttons = {}
        self.accent_buttons = {}
        for (title, value), asset in zip(ACCENTS.items(), ("aee60.svg", "58cb7.svg", "75ed9.svg")):
            b = _option(title, asset="settings-ver5/" + asset)
            b.setProperty("fixedColorIcon", True)
            b.clicked.connect(lambda checked=False, v=value: self.update_draft(accent=v))
            self.accent_buttons[title] = b
        self.accent_field = self._color_field("accent", "직접 선택")
        height = self.fields["accent"].minimumHeight()
        for b in self.accent_buttons.values():
            b.setFixedHeight(height)
            b.setStyleSheet(f"QPushButton {{padding:0 18px;min-height:{height - 2}px;max-height:{height - 2}px;}}")
        accent.body.addWidget(DashboardGrid([*self.accent_buttons.values(), self.accent_field],
                                            minimum_width=144, maximum_columns=4, spacing=12,
                                            captions={self.accent_field: self.custom_accent_label}))
        settings.addWidget(accent)
        dashboard = card("대시보드 색상")
        self.dashboard_palette_buttons = self._palette_options(dashboard, "dashboard_palette")
        dashboard.body.addWidget(DashboardGrid([self._color_field(key, title) for key, title in DASHBOARD_FIELDS],
                                               minimum_width=150, maximum_columns=3))
        settings.addWidget(dashboard)
        heat = card("버전 히트맵 색상")
        self.palette_buttons = self._palette_options(heat, "heatmap_palette")
        heat.body.addWidget(DashboardGrid([self._color_field(key, title) for key, title in HEATMAP_FIELDS],
                                          minimum_width=150, maximum_columns=3))
        settings.addWidget(heat)
        background = card("배경 · 카드 색상")
        background.body.addWidget(DashboardGrid([self._color_field("canvas", "화면 배경"), self._color_field("surface", "카드 배경")], minimum_width=180, maximum_columns=2))
        settings.addWidget(background)
        self.reset_button, self.cancel_button, self.save_button = button("기본값으로"), button("취소"), button("저장", primary=True)
        self.reset_button.clicked.connect(lambda: self._reset_appearance())
        self.cancel_button.clicked.connect(self.cancel_appearance)
        self.save_button.clicked.connect(self.save_appearance)
        settings.addWidget(actions(self.reset_button, None, self.cancel_button, self.save_button))
        self.feedback = note()
        settings.addWidget(self.feedback)
        self.preview = AppearancePreview()
        self.preview_column = PreviewColumn(self.preview, self.scroll_area, breakpoint=1040)
        self.appearance_row = SplitRow(controls, self.preview_column, breakpoint=self.preview_column.breakpoint)
        body.addWidget(self.appearance_row)
        return page

    def _palette_options(self, panel, attribute):
        choices = {}
        for key, title in (("standard", "기본"), ("distinct", "색각 보정"), ("custom", "직접 선택")):
            choice = _option(title)
            choice.clicked.connect(lambda checked=False, k=key: self.update_draft(**{attribute: k}))
            choices[key] = choice
        panel.body.addWidget(DashboardGrid(list(choices.values()), minimum_width=140, maximum_columns=3))
        return choices

    def _color_field(self, key, title):
        host = QWidget()
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        field = text_field(title)
        field.setMaxLength(7)
        field.setMinimumWidth(0)
        field.setPlaceholderText("#RRGGBB")
        field.editingFinished.connect(lambda: self._edit_color(key))
        if key == "canvas":
            field.textEdited.connect(self._preview_canvas_input)
        heading = note(title)
        heading.setBuddy(field)
        if key == "accent":
            self.custom_accent_label = heading
        else:
            box.addWidget(heading)
        picker_button = button("")
        picker_button.setAccessibleName(f"{title} 색상 팔레트 열기")
        picker_button.setProperty("fixedColorIcon", True)
        picker_button.setIconSize(QSize(18, 18))
        side = field.minimumHeight()
        picker_button.setFixedSize(side, side)
        picker_button.setStyleSheet(f"QPushButton {{padding:0;min-height:{side - 2}px;max-height:{side - 2}px;}}")
        picker_button.clicked.connect(lambda checked=False: self._open_color_picker(key, title))
        input_row = QWidget()
        input_row.setFixedHeight(side)
        row = QHBoxLayout(input_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(field, 1)
        row.addWidget(picker_button)
        box.addWidget(input_row)
        self.fields[key] = field
        self.color_picker_buttons[key] = picker_button
        return host

    def _open_color_picker(self, key, title):
        if self.operation.busy:
            return
        self._select_color_preview(key)
        dark = self._dark()
        colors = self.controller.tokens if self.controller else self.saved.colors(dark)
        dialog = SettingsColorDialog(self.draft.colors(dark)[key], title, colors, self)
        dialog.colorSelected.connect(lambda color: self._apply_color(key, color.name(), dark=dark))
        if key == "canvas":
            dialog.currentColorChanged.connect(lambda color: self._preview_canvas(color.name()))
            dialog.finished.connect(lambda _: self._preview_canvas())
        dialog.open()

    def _apply_color(self, key, value, *, dark):
        if value.lower() == self.draft.colors(dark)[key].lower():
            return
        self.draft = self.draft.with_colors(dark, **{key: value})
        self._select_color_preview(key)
        self._render()

    def _select_color_preview(self, key):
        if key in HEATMAP_KEYS:
            self.preview.show_page("heatmap")
        elif key in DASHBOARD_KEYS:
            self.preview.show_page("dashboard")

    def _render_color_picker(self, key, value):
        control = self.color_picker_buttons[key]
        if control.property("colorValue") == value:
            return
        # 테마가 바뀌어도 이 스와치는 원래 선택한 RGB를 그대로 보여 준다.
        swatch = QPixmap(18, 18)
        swatch.fill(QColor(value))
        painter = QPainter(swatch)
        painter.setPen(QColor(readable_color(SECONDARY, value, 3)))
        painter.drawRect(0, 0, 17, 17)
        painter.end()
        control.setIcon(QIcon(swatch))
        control.setProperty("colorValue", value)
        control.setToolTip(f"색상 팔레트 열기 · {value.upper()}")
        control.setAccessibleDescription(f"현재 미리보기 색상 {value.upper()}")

    def _edit_color(self, key):
        if self._rendering:
            return
        try:
            self._apply_color(key, self.fields[key].text(), dark=self._dark())
        except ValueError as error:
            self.feedback.setText(str(error))

    def update_draft(self, **values):
        colors = {key: values.pop(key) for key in tuple(values) if key in self.fields}
        self.draft = replace(self.draft, **values)
        self.draft = self.draft.with_colors(self._dark(), **colors)
        if "heatmap_palette" in values or set(colors) & set(HEATMAP_KEYS):
            self.preview.show_page("heatmap")
        elif "dashboard_palette" in values or set(colors) & set(DASHBOARD_KEYS):
            self.preview.show_page("dashboard")
        self._render()

    def _dark(self):
        return self.draft.mode == "dark" or (self.draft.mode == "system"
                and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)

    def _sync_applied_font_size(self):
        applied = self.controller.appearance.font_size
        if self.saved.font_size == applied:
            return
        follow_applied = self.draft.font_size == self.saved.font_size
        self.saved = replace(self.saved, font_size=applied)
        if follow_applied:
            self.draft = replace(self.draft, font_size=applied)
            self.preview.render(self.draft.colors(self._dark()), self.draft.font_scale)
        self._render_font_sizes()

    def _render_font_sizes(self):
        for key, b in self.font_size_buttons.items():
            b.setChecked(key == self.draft.font_size)

    def enter(self):
        """다시 들어오면 모든 선택과 색상을 앱에 적용된 상태로 복원한다."""
        self._canvas_preview_active = True
        self.preview_column.refresh()
        if self.operation.busy:
            return
        applied = self.controller.appearance if self.controller else self.saved
        self.saved = applied
        if self.draft != applied:
            self.draft = applied
            self._render()
        else:
            self._preview_canvas()

    def leave(self):
        self._canvas_preview_active = False
        self._set_canvas_preview(None)

    def _preview_canvas_input(self, text):
        color = QColor(text)
        self._preview_canvas(color.name() if len(text) == 7 and text.startswith("#") and color.isValid() else None)

    def _preview_canvas(self, candidate=None):
        if not self._canvas_preview_active:
            return
        dark = self._dark()
        value = candidate or self.draft.colors(dark)["canvas"]
        self._set_canvas_preview(value if value != self.saved.colors(dark)["canvas"] else None)

    def _set_canvas_preview(self, color):
        if self._set_preview_background:
            self._set_preview_background(color)

    def _render(self):
        self._rendering = True
        dark = self._dark()
        colors = self.draft.colors(dark)
        for key, b in self.mode_buttons.items():
            b.setChecked(key == self.draft.mode)
        for buttons, active in ((self.dashboard_palette_buttons, self.draft.dashboard_palette),
                                (self.palette_buttons, self.draft.heatmap_palette)):
            for key, b in buttons.items():
                b.setChecked(key == active)
        # 다크 기본 Blue는 밝은 모드의 프리셋 HEX와 다른 기본값을 사용한다.
        preset = next((name for name, value in ACCENTS.items() if value == colors["accent"]),
                      "Blue" if colors["accent"] == Appearance().colors(dark)["accent"] else None)
        for key, b in self.accent_buttons.items():
            b.setChecked(key == preset)
        self._render_font_sizes()
        for key, field in self.fields.items():
            field.setText(colors[key].upper())
            self._render_color_picker(key, colors[key])
        self.preview.render(colors, self.draft.font_scale)
        self._rendering = False
        self._preview_canvas()

    def _collect(self):
        # 저장 클릭은 editingFinished 발생 순서와 무관하게 모든 입력값을 검증한다.
        dark = self._dark()
        colors = self.draft.colors(dark)
        changes = {key: field.text() for key, field in self.fields.items() if field.text().lower() != colors[key].lower()}
        return self.draft.with_colors(dark, **changes)

    def save_appearance(self):
        if self.operation.busy:
            return False
        try:
            draft = self._collect()
        except ValueError as error:
            self.feedback.setText(str(error))
            return False
        self.feedback.clear()
        if self.toast is not None:
            self.toast.hide()

        def stored(done):
            def applied():
                self.saved = self.draft = draft
                self._render()
                done()
                host = self.window()
                if self.toast is None or self.toast.parentWidget() is not host:
                    if self.toast is not None:
                        self.toast.deleteLater()
                    self.toast = Toast(host)
                self.toast.show_message("저장 완료 · 모든 화면에 적용했습니다.")
            if self.controller:
                self.controller.apply_async(draft, applied, progress_window=self.operation.dialog)
            else:
                applied()
        return self.operation.start(self.store, "appearance", draft, "화면 설정 저장 중", stored, self.feedback.setText)

    def cancel_appearance(self):
        self.draft = self.saved
        self._render()
        self.feedback.setText("저장된 설정으로 되돌렸습니다.")

    def _reset_appearance(self):
        self.draft = Appearance()
        self._render()
        self.feedback.setText("기본값 미리보기 · 저장을 누르면 적용됩니다.")
