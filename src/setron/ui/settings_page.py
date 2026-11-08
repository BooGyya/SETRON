"""VER5 10 기준·설정. 개인 설정은 로컬 저장, 업무 원본과 판정은 보존한다."""

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.appearance import contrasting, manager
from setron.ui.appearance_data import ACCENTS, MENU_ORDER, Appearance, PersonalSettings
from setron.ui.components import button, label
from setron.ui.dashboard import DashboardGrid, SplitRow
from setron.ui.import_dialogs import ImportDialog
from setron.ui.main_window import MENUS, PageStack
from setron.ui.settings_location import LocationPanel
from setron.ui.theme import SECONDARY, icon, svg_size
from setron.ui.version_management_widgets import actions, card, note, set_rows, table, tabs, text_field


TITLES = {menu.key: menu.title for menu in MENUS}


class PersonalDialog(ImportDialog):
    def __init__(self, catalog, personal, parent=None):
        super().__init__("나의 라인 · 설비군", parent)
        self.checks = {}
        for key, title, options in (("lines", "Line", catalog.lines), ("groups", "설비군", catalog.groups)):
            self.body.addWidget(label(title, 15, medium=True))
            choices = []
            for option in options:
                check = QCheckBox(option.label)
                check.setChecked(personal[key] is None or option.key in personal[key])
                self.checks[key, option.key] = check
                choices.append(check)
            if choices:
                self.body.addWidget(DashboardGrid(choices, minimum_width=140, maximum_columns=3))
            else:
                self.body.addWidget(note("등록된 선택지가 없습니다."))
        self.body.addWidget(note("선택한 항목은 대시보드·히트맵의 조회 선택지에만 적용됩니다. 저장 데이터는 삭제하지 않습니다."))
        self.add_action("전체 선택", lambda: [check.setChecked(True) for check in self.checks.values()])
        self.add_action("취소", self.reject, default=True)
        self.add_action("저장", self.accept, primary=True)
        self.finish_layout()

    def values(self):
        return {key: [value for (kind, value), check in self.checks.items() if kind == key and check.isChecked()]
                for key in ("lines", "groups")}


class SettingsPage(QWidget):
    personal_changed = Signal(object)
    targets_requested = Signal()
    management_requested = Signal(str)

    def __init__(self, source, *, store=None, controller=None):
        super().__init__()
        self.source = source
        self.controller = controller or manager()
        self.store = store or (self.controller.store if self.controller else PersonalSettings())
        self.saved = self.store.appearance()
        self.draft = self.saved
        self.personal = self.store.personal()
        self.order_draft = list(self.personal["order"])
        self.home_draft = self.personal["home"]
        self._rendering = False
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tabs = tabs(("위험 기준", "버전 계열 · 연결", "화면 · 테마"), "기준·설정 화면 선택")
        self.tabs.setUsesScrollButtons(False)
        body.addWidget(self.tabs)
        self.stack = PageStack()
        self.risk_page = self._risk()
        self.family_page = self._families()
        self.location = LocationPanel(source)
        self.location.back_requested.connect(lambda: self.stack.setCurrentWidget(self.family_page))
        self.appearance_page = self._appearance()
        self.views = (self.risk_page, self.family_page, self.appearance_page)
        for view in (*self.views, self.location):
            self.stack.addWidget(view)
        body.addWidget(self.stack)
        self.tabs.currentChanged.connect(lambda i: self.stack.setCurrentWidget(self.views[i]))
        self.tabs.setCurrentIndex(2)
        self.stack.setCurrentWidget(self.appearance_page)
        self._render()

    def _risk(self):
        panel = card("위험 기준")
        panel.body.addWidget(note("환경·안전 필수 Item, 결함 Item, 직접 지정 위험 버전을 기준으로 판정합니다. 목표 N 달성과 별개입니다."))
        self.risk_manage = button("위험 기준 관리")
        panel.body.addWidget(actions(self.risk_manage))
        self.risk_manage.clicked.connect(lambda: self.management_requested.emit("risk_rule"))
        model, view = table(("기준", "판정 방법"), "위험 기준 종류", (230, 630))
        set_rows(model, view, (("환경·안전 필수 Item", "필수 Item이 적용되지 않은 설비"),
                               ("결함 Item", "결함이 포함된 시작~해제 버전 범위"),
                               ("위험 버전 직접 지정", "사용자가 지정한 버전과 적용 범위")))
        panel.body.addWidget(view)
        return panel

    def _families(self):
        panel = card("버전 계열 · 연결")
        panel.body.addWidget(note("같은 계열의 확인된 순서로 버전을 비교합니다. 이름이 비슷하다는 이유로 다른 계열을 합치지 않습니다."))
        goal, edit, location = button("버전(N) 기준 입력"), button("계열·적용 근거 관리"), button("위치 관리")
        goal.clicked.connect(self.targets_requested.emit)
        edit.clicked.connect(lambda: self.management_requested.emit("release_version"))
        location.clicked.connect(lambda: self.stack.setCurrentWidget(self.location))
        panel.body.addWidget(actions(goal, edit, location))
        model, view = table(("SW 계열", "등록 릴리즈", "목표 기준"), "버전 계열 연결", (260, 220, 350))
        rows = []
        from setron.ui.dashboard_data import family_name
        for key, family in self.source.families.items():
            rows.append((family_name(self.source, key), f"{len(family.releases)}개", "모델별 버전(N) 기준 입력에서 확인"))
        set_rows(model, view, tuple(rows))
        panel.body.addWidget(view)
        return panel

    def _appearance(self):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        body.addWidget(note("개인 보기 설정"))
        personal = card("메뉴 순서 · 화면 배치", outlined=False)
        personal.body.addWidget(note("개인 화면에만 적용됩니다."))
        personal.body.addWidget(label("메뉴 순서와 첫 화면의 정보 배치를 개인 설정으로 관리합니다.", wrap=True))
        self.edit_layout = button("메뉴 · 배치 편집")
        personal.body.addWidget(actions(self.edit_layout))
        self.layout_editor = QWidget()
        editor = QVBoxLayout(self.layout_editor)
        editor.setContentsMargins(0, 4, 0, 0)
        self.menu_rows = QWidget()
        self.menu_body = QVBoxLayout(self.menu_rows)
        self.menu_body.setContentsMargins(0, 0, 0, 0)
        self.menu_preview = label("", wrap=True)
        editor.addWidget(DashboardGrid([self.menu_rows, self.menu_preview], minimum_width=380, maximum_columns=2))
        editor.addWidget(label("첫 화면 배치", 16, medium=True))
        self.home_buttons = {}
        for key, title in (("metrics", "지표 우선"), ("heatmap", "히트맵 우선")):
            b = button(title)
            b.setCheckable(True)
            b.clicked.connect(lambda checked=False, k=key: self._set_home(k))
            self.home_buttons[key] = b
        editor.addWidget(actions(*self.home_buttons.values()))
        self.home_preview = note()
        editor.addWidget(self.home_preview)
        self.personal_save, self.personal_reset, self.personal_cancel = button("개인 설정 저장", primary=True), button("기본값으로"), button("취소")
        editor.addWidget(actions(self.personal_save, self.personal_reset, self.personal_cancel))
        self.personal_save.clicked.connect(self.save_personal)
        self.personal_cancel.clicked.connect(self.cancel_personal)
        self.personal_reset.clicked.connect(lambda: self._reset_personal())
        personal.body.addWidget(self.layout_editor)
        self.layout_editor.hide()
        self.edit_layout.clicked.connect(self.open_layout)
        body.addWidget(personal)
        controls = QWidget()
        settings = QVBoxLayout(controls)
        settings.setContentsMargins(0, 0, 0, 0)
        settings.setSpacing(20)
        display = card("표시 모드")
        self.mode_buttons = {}
        for key, title in (("light", "밝게"), ("dark", "어둡게"), ("system", "시스템")):
            b = button(title)
            b.setCheckable(True)
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
        accent = card("포인트 색상")
        self.fields = {}
        self.legend_widgets = []
        self.accent_buttons = {}
        for (title, value), asset in zip(ACCENTS.items(), ("aee60.svg", "58cb7.svg", "75ed9.svg")):
            b = button(title, asset="settings-ver5/" + asset)
            b.setProperty("fixedColorIcon", True)
            b.clicked.connect(lambda checked=False, v=value: self.update_draft(accent=v))
            self.accent_buttons[title] = b
        self.accent_field = self._color_field("accent", "직접 선택")
        accent.body.addWidget(DashboardGrid([*self.accent_buttons.values(), self.accent_field], minimum_width=130, maximum_columns=4, spacing=12))
        settings.addWidget(accent)
        heat = card("히트맵 색상")
        self.palette_buttons = {}
        for key, title in (("standard", "기본"), ("distinct", "색 구분 강화"), ("custom", "직접 선택")):
            b = button(title)
            b.setCheckable(True)
            b.clicked.connect(lambda checked=False, k=key: self.update_draft(palette=k))
            self.palette_buttons[key] = b
        heat.body.addWidget(DashboardGrid(list(self.palette_buttons.values()), minimum_width=140, maximum_columns=3))
        heat.body.addWidget(DashboardGrid([self._color_field(key, title) for key, title in
            (("met", "목표 달성"), ("below", "목표 미달"), ("risk", "안전·결함 후보"))], minimum_width=150, maximum_columns=3))
        heat.body.addWidget(DashboardGrid([self._color_field("rate", "달성률 강조색"),
            *self._legend()], minimum_width=130, maximum_columns=4, spacing=8))
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
        self.appearance_row = SplitRow(controls, self.preview, breakpoint=1040)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.appearance_row.box.setAlignment(self.preview, Qt.AlignmentFlag.AlignTop)
        body.addWidget(self.appearance_row)
        mine = card("나의 라인 · 설비군", outlined=False)
        self.mine_button = button("라인·설비군 선택")
        self.mine_button.clicked.connect(self.open_personal)
        mine.body.addWidget(actions(self.mine_button))
        body.addWidget(mine)
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
        heading = note(title)
        heading.setBuddy(field)
        box.addWidget(heading)
        box.addWidget(field)
        self.fields[key] = field
        return host

    def _edit_color(self, key):
        if self._rendering:
            return
        try:
            self.update_draft(**{key: self.fields[key].text(), **({"palette": "custom"} if key in ("met", "below", "risk") else {})})
        except ValueError as error:
            self.feedback.setText(str(error))

    def update_draft(self, **values):
        self.draft = replace(self.draft, **values)
        self._render()

    def _render(self):
        self._rendering = True
        dark = self.draft.mode == "dark" or (self.draft.mode == "system"
            and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        colors = self.draft.colors(dark)
        for key, b in self.mode_buttons.items():
            b.setChecked(key == self.draft.mode)
            b.setProperty("soft", key == self.draft.mode)
            b.style().unpolish(b)
            b.style().polish(b)
        for key, b in self.palette_buttons.items():
            b.setChecked(key == self.draft.palette)
            b.setProperty("soft", key == self.draft.palette)
            b.style().unpolish(b)
            b.style().polish(b)
        for key, field in self.fields.items():
            field.setText(colors[key].upper())
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
        self._rendering = False

    def _collect(self):
        # 저장 클릭은 editingFinished 발생 순서와 무관하게 모든 입력값을 검증한다.
        colors = self.draft.colors(self.draft.mode == "dark" or self.draft.mode == "system"
            and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        changes = {key: field.text() for key, field in self.fields.items() if field.text().lower() != colors[key].lower()}
        if set(changes) & {"met", "below", "risk"}:
            changes["palette"] = "custom"
        return replace(self.draft, **changes)

    def save_appearance(self):
        try:
            draft = self._collect()
            if self.controller:
                self.controller.apply(draft, persist=True)
            else:
                self.store.save("appearance", draft)
        except (ValueError, OSError) as error:
            self.feedback.setText(str(error))
            return False
        self.saved = self.draft = draft
        self.feedback.setText("저장 완료 · 모든 화면에 적용했습니다.")
        self._render()
        return True

    def cancel_appearance(self):
        self.draft = self.saved
        self._render()
        self.feedback.setText("저장된 설정으로 되돌렸습니다.")

    def _reset_appearance(self):
        self.draft = Appearance()
        self._render()
        self.feedback.setText("기본값 미리보기 · 저장을 누르면 적용됩니다.")

    def open_layout(self):
        self.order_draft, self.home_draft = list(self.personal["order"]), self.personal["home"]
        self._render_layout()
        self.layout_editor.show()

    def _render_layout(self):
        while self.menu_body.count():
            item = self.menu_body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.menu_up, self.menu_down = {}, {}
        for index, key in enumerate(self.order_draft):
            up, down = button("위로"), button("아래로")
            up.setAccessibleName(TITLES[key] + " 위로")
            down.setAccessibleName(TITLES[key] + " 아래로")
            up.setEnabled(index > 0)
            down.setEnabled(index < len(self.order_draft) - 1)
            up.clicked.connect(lambda checked=False, i=index: self.move_menu(i, -1))
            down.clicked.connect(lambda checked=False, i=index: self.move_menu(i, 1))
            row = actions(label(f"{index+1}  {TITLES[key]}"), None, up, down)
            self.menu_body.addWidget(row)
            self.menu_up[key], self.menu_down[key] = up, down
        self.menu_preview.setText("미리보기 · 저장 전 변경 사항\n\n" + "\n\n".join(f"{i+1}  {TITLES[key]}" for i, key in enumerate(self.order_draft)))
        self._set_home(self.home_draft)

    def move_menu(self, index, offset):
        other = index + offset
        if 0 <= index < len(self.order_draft) and 0 <= other < len(self.order_draft):
            self.order_draft[index], self.order_draft[other] = self.order_draft[other], self.order_draft[index]
            self._render_layout()

    def _set_home(self, key):
        self.home_draft = key
        for name, b in self.home_buttons.items():
            b.setChecked(name == key)
        titles = ["KPI 카드: 전체 설비 · 목표 달성률 · 위험 후보", "Site / Fab / Line별 버전 히트맵", "최근 버전 변경 현황"]
        if key == "heatmap":
            titles[0], titles[1] = titles[1], titles[0]
        self.home_preview.setText("\n".join(f"{i+1}. {title}" for i, title in enumerate(titles)))

    def _reset_personal(self):
        self.order_draft, self.home_draft = list(MENU_ORDER), "metrics"
        self._render_layout()

    def save_personal(self):
        candidate = {**self.personal, "order": tuple(self.order_draft), "home": self.home_draft}
        try:
            self.store.save("personal", candidate)
        except OSError as error:
            self.feedback.setText(str(error))
            return False
        self.personal = candidate
        self.personal_changed.emit(candidate)
        self.layout_editor.hide()
        return True

    def cancel_personal(self):
        self.order_draft, self.home_draft = list(self.personal["order"]), self.personal["home"]
        self.layout_editor.hide()

    def open_personal(self):
        dialog = PersonalDialog(self.source.catalog, self.personal, self)
        self.personal_dialog = dialog
        def finish(result):
            if result == dialog.DialogCode.Accepted:
                candidate = {**self.personal, **dialog.values()}
                try:
                    self.store.save("personal", candidate)
                except OSError as error:
                    self.feedback.setText(str(error))
                    return
                self.personal = candidate
                self.personal_changed.emit(candidate)
            dialog.deleteLater()
        dialog.finished.connect(finish)
        dialog.open()
