"""VER5 10 설정. 개인 설정은 로컬 저장, 업무 원본과 판정은 보존한다."""

from dataclasses import replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QDialogButtonBox, QFrame, QHBoxLayout, QVBoxLayout, QWidget,
)

from setron.ui.appearance import contrasting, manager, refresh_widget_font
from setron.ui.appearance_data import ACCENTS, FONT_SCALES, Appearance, PersonalSettings
from setron.ui.components import button, label
from setron.ui.dashboard import DashboardGrid, SplitRow
from setron.ui.settings_preview import PreviewColumn
from setron.ui.settings_tasks import SettingsSave
from setron.ui.theme import PRIMARY, SECONDARY
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
        body.addWidget(note("개인 보기 설정"))
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
        self.legend_widgets = []
        self.accent_buttons = {}
        for (title, value), asset in zip(ACCENTS.items(), ("aee60.svg", "58cb7.svg", "75ed9.svg")):
            b = _option(title, asset="settings-ver5/" + asset)
            b.setProperty("fixedColorIcon", True)
            b.clicked.connect(lambda checked=False, v=value: self.update_draft(accent=v))
            self.accent_buttons[title] = b
        self.accent_field = self._color_field("accent", "직접 선택")
        for b in self.accent_buttons.values():
            b.setFixedHeight(self.fields["accent"].minimumHeight())
        accent.body.addWidget(DashboardGrid([*self.accent_buttons.values(), self.accent_field],
                                            minimum_width=144, maximum_columns=4, spacing=12,
                                            alignment=Qt.AlignmentFlag.AlignBottom))
        settings.addWidget(accent)
        heat = card("히트맵 색상")
        self.palette_buttons = {}
        for key, title in (("standard", "기본"), ("distinct", "색 구분 강화"), ("custom", "직접 선택")):
            b = _option(title)
            b.clicked.connect(lambda checked=False, k=key: self.update_draft(palette=k))
            self.palette_buttons[key] = b
        heat.body.addWidget(DashboardGrid(list(self.palette_buttons.values()), minimum_width=140, maximum_columns=3))
        heat.body.addWidget(DashboardGrid([self._color_field(key, title) for key, title in
            (("met", "목표 달성"), ("below", "목표 미달"), ("risk", "안전·결함 후보"))], minimum_width=150, maximum_columns=3))
        heat.body.addWidget(DashboardGrid([self._color_field("rate", "달성률 강조색"),
            *self._legend()], minimum_width=144, maximum_columns=4, spacing=8))
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
        self.preview = card("미리보기")
        self.preview.setMinimumWidth(0)
        self.preview.setProperty("themePreview", True)
        preview_top = actions(button("대시보드"), None, button("조회 적용", primary=True))
        preview_top.setEnabled(False)
        self.preview.body.addWidget(preview_top)
        self.preview_metric = card()
        self.preview_metric.body.addWidget(label("등록 SW N 달성률", 14, medium=True))
        self.preview_value = label("60.0 %", 46, medium=True)
        self.preview_metric.body.addWidget(self.preview_value)
        self.preview_metric.body.addWidget(note("48 / 80대 · 미확인 포함 · 미리보기 예시"))
        self.preview.body.addWidget(self.preview_metric)
        self.preview_tiles = []
        for code, version, caption, key in (("DM-001", "v90.1.0.43", "목표 달성", "met"),
                ("DM-007", "v90.1.0.41", "목표 미달", "below"), ("DM-010", "미입력", "확인 필요", "below"),
                ("DM-006 · 위험", "v2296.9_k", "목표 달성", "risk")):
            tile = card(outlined=False)
            tile.setMinimumHeight(106)
            tile.body.setSpacing(6)
            tile.body.addWidget(label(code, 14, medium=True))
            tile.body.addWidget(label(version, 13))
            tile.body.addWidget(label(caption, 13))
            self.preview_tiles.append((tile, key))
        self.preview.body.addWidget(DashboardGrid([tile for tile, _ in self.preview_tiles], minimum_width=145, maximum_columns=2, spacing=12))
        self.preview.body.addWidget(note("초록 달성 · 노랑 미달/확인 필요 · 빨강 위험 후보"))
        self.preview.body.addWidget(DashboardGrid(self._legend(), minimum_width=110, maximum_columns=3, spacing=6))
        self.preview_column = PreviewColumn(self.preview, self.scroll_area, breakpoint=1040)
        self.appearance_row = SplitRow(controls, self.preview_column, breakpoint=self.preview_column.breakpoint)
        body.addWidget(self.appearance_row)
        return page

    def _legend(self):
        result = []
        for key, caption in (("met", "목표 달성"), ("below", "미달 · 확인 필요"), ("risk", "위험 후보")):
            item = label(caption, 12, wrap=True)
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setMinimumHeight(40)
            item.setProperty("themePreview", True)
            self.legend_widgets.append((item, key))
            result.append(item)
        return result

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
        box.addWidget(heading)
        picker_button = button("")
        picker_button.setAccessibleName(f"{title} 색상 팔레트 열기")
        picker_button.setProperty("fixedColorIcon", True)
        picker_button.setIconSize(QSize(18, 18))
        picker_button.setFixedSize(field.minimumHeight(), field.minimumHeight())
        picker_button.setStyleSheet("QPushButton {padding:0;}")
        picker_button.clicked.connect(lambda checked=False: self._open_color_picker(key, title))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(field, 1)
        row.addWidget(picker_button)
        box.addLayout(row)
        self.fields[key] = field
        self.color_picker_buttons[key] = picker_button
        return host

    def _open_color_picker(self, key, title):
        if self.operation.busy:
            return
        dark = self._dark()
        dialog = QColorDialog(QColor(self.draft.colors(dark)[key]), self)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        dialog.setWindowTitle(f"{title} 색상 선택")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if buttons := dialog.findChild(QDialogButtonBox):
            for role, caption in ((QDialogButtonBox.StandardButton.Ok, "확인"),
                                  (QDialogButtonBox.StandardButton.Cancel, "취소")):
                if control := buttons.button(role):
                    control.setText(caption)
        dialog.colorSelected.connect(lambda color: self._apply_color(key, color.name(), dark=dark))
        if key == "canvas":
            dialog.currentColorChanged.connect(lambda color: self._preview_canvas(color.name()))
            dialog.finished.connect(lambda _: self._preview_canvas())
        dialog.open()

    def _apply_color(self, key, value, *, dark):
        if value.lower() == self.draft.colors(dark)[key].lower():
            return
        draft = replace(self.draft, palette="custom") if key in ("met", "below", "risk") else self.draft
        self.draft = draft.with_colors(dark, **{key: value})
        self._render()

    def _render_color_picker(self, key, value):
        control = self.color_picker_buttons[key]
        if control.property("colorValue") == value:
            return
        # 테마가 바뀌어도 이 스와치는 원래 선택한 RGB를 그대로 보여 준다.
        swatch = QPixmap(18, 18)
        swatch.fill(QColor(value))
        painter = QPainter(swatch)
        painter.setPen(QColor(SECONDARY))
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
            self._resize_preview_fonts()
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

    def _resize_preview_fonts(self):
        for child in self.preview.findChildren(QWidget):
            child.setProperty("setronPreviewFontScale", self.draft.font_scale)
            refresh_widget_font(child)

    def _render(self):
        self._rendering = True
        dark = self._dark()
        colors = self.draft.colors(dark)
        for key, b in self.mode_buttons.items():
            b.setChecked(key == self.draft.mode)
        for key, b in self.palette_buttons.items():
            b.setChecked(key == self.draft.palette)
        # 다크 기본 Blue는 밝은 모드의 프리셋 HEX와 다른 기본값을 사용한다.
        preset = next((name for name, value in ACCENTS.items() if value == colors["accent"]),
                      "Blue" if colors["accent"] == Appearance().colors(dark)["accent"] else None)
        for key, b in self.accent_buttons.items():
            b.setChecked(key == preset)
        self.custom_accent_label.setStyleSheet(f"color:{PRIMARY if preset is None else SECONDARY};")
        self._render_font_sizes()
        for key, field in self.fields.items():
            field.setText(colors[key].upper())
            self._render_color_picker(key, colors[key])
        self.preview.setStyleSheet(f"QFrame#card {{background:{colors['surface']};border:1px solid {colors['line']};border-radius:16px;}} QLabel {{color:{colors['ink']};}}")
        self.preview_metric.setProperty("themePreview", True)
        self.preview_metric.setStyleSheet(f"QFrame#card {{background:{colors['selected']};border:1px solid {colors['line']};border-radius:16px;}}")
        self.preview_value.setProperty("themePreview", True)
        self.preview_value.setStyleSheet(f"color:{colors['rate']};")
        for tile, key in self.preview_tiles:
            tile.setProperty("themePreview", True)
            tile.setStyleSheet(f"QFrame#card {{background:{colors[key]};border:1px solid {colors['line']};border-radius:4px;}} QLabel {{color:{contrasting(colors[key])};}}")
        for item, key in self.legend_widgets:
            item.setStyleSheet(f"background:{colors[key]};color:{contrasting(colors[key])};border:1px solid {colors['line']};border-radius:4px;padding:4px;")
        for child in self.preview.findChildren(QWidget):
            child.setProperty("themePreview", True)
            if child not in (self.preview_value, *(item for item, _ in self.legend_widgets)) and not child.styleSheet().startswith("QFrame#card"):
                child.setStyleSheet("")
        self._resize_preview_fonts()
        self._rendering = False
        self._preview_canvas()

    def _collect(self):
        # 저장 클릭은 editingFinished 발생 순서와 무관하게 모든 입력값을 검증한다.
        colors = self.draft.colors(self.draft.mode == "dark" or self.draft.mode == "system"
            and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        changes = {key: field.text() for key, field in self.fields.items() if field.text().lower() != colors[key].lower()}
        if set(changes) & {"met", "below", "risk"}:
            changes["palette"] = "custom"
        palette = changes.pop("palette", self.draft.palette)
        return replace(self.draft, palette=palette).with_colors(self._dark(), **changes)

    def save_appearance(self):
        if self.operation.busy:
            return False
        try:
            draft = self._collect()
        except ValueError as error:
            self.feedback.setText(str(error))
            return False
        def stored(done):
            def applied():
                self.saved = self.draft = draft
                self.feedback.setText("저장 완료 · 모든 화면에 적용했습니다.")
                self._render()
                done()
            if self.controller:
                self.controller.apply_async(draft, applied)
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
