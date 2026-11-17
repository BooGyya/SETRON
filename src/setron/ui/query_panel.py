"""공통 조회 패널. 날짜와 필터 초안은 조회 버튼에서 한 번에 적용한다."""

from collections import Counter
from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.components import Card, DisclosureArrow, StatusBadge, button, label
from setron.ui.calendar_widgets import DateEdit
from setron.ui.date_range import DatePickerEdit
from setron.ui.query_filter_popup import QueryFilterPopup
from setron.ui.query_state import UNASSIGNED, QueryCatalog, QuerySelection, QueryState, selection_keys, version_filter_caption
from setron.ui.theme import ASSETS, INK, LINE, PRIMARY, SECONDARY, SOFT, font


FILTER_CAPTIONS = {"fab_key": "분류", "site_key": "사업장", "line_key": "라인", "group_key": "설비군"}


class _WrapRow(QWidget):
    """좁은 창에서 필드/버튼을 줄바꿈한다. 숨겨진 복제 화면을 만들지 않는다."""

    def __init__(self, widgets, minimum_width, *, fields=False):
        super().__init__()
        self.widgets = widgets
        self.minimum_width = minimum_width
        self.fields = fields
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._arrange(len(widgets))

    def minimumSizeHint(self):
        # 현재 한 줄의 너비를 부모 최소 너비로 강제하면, 좁아져야 발생하는 줄바꿈이 막힌다.
        return QSize(0, self.grid.minimumSize().height())

    def _arrange(self, columns, *, force=False):
        if self.columns == columns and not force:
            return
        for index in range(max(self.columns, columns) + 1):
            self.grid.setColumnStretch(index, 0)
        for widget in self.widgets:
            self.grid.removeWidget(widget)
        for index, widget in enumerate(self.widgets):
            self.grid.addWidget(widget, index // columns, index % columns)
        if self.fields:
            # 고정 너비인 조회 도구 외에는 남는 폭을 나눠 쓴다.
            self.grid.setAlignment(self.widgets[-1], Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
            for index in range(columns):
                fixed = all(w.maximumWidth() < 10000 for w in self.widgets[index::columns])
                self.grid.setColumnStretch(index, 0 if fixed else 1)
        else:
            self.grid.setColumnStretch(columns, 1)
        self.columns = columns
        self.updateGeometry()

    def reflow(self, width=None, *, force=False):
        width = self.width() if width is None else width
        columns = max(1, min(len(self.widgets), (width + 12) // (self.minimum_width + 12)))
        if self.fields:
            needed = sum(max(w.minimumWidth(), self.minimum_width) if w.maximumWidth() >= 10000 else w.maximumWidth()
                         for w in self.widgets) + 12 * (len(self.widgets) - 1)
            columns = len(self.widgets) if width >= needed else min(3, columns)
        self._arrange(columns, force=force)

    def resizeEvent(self, event):
        self.reflow(event.size().width())
        super().resizeEvent(event)


class _FilterButton(QPushButton):
    def __init__(self, caption):
        super().__init__()
        self.dimension = caption
        self.caption = "전체"
        self.setFont(font())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setObjectName("queryFilter")
        self.setStyleSheet(
            f"QPushButton#queryFilter {{background:{SOFT};border:1px solid {LINE};border-radius:10px;"
            "padding:0 36px 0 12px;text-align:left;min-height:36px;max-height:36px;}"
            f"QPushButton#queryFilter:hover, QPushButton#queryFilter:focus, QPushButton#queryFilter:pressed "
            f"{{border-color:{PRIMARY};}}")
        self.arrow = DisclosureArrow()
        self.arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content = QHBoxLayout(self)
        content.setContentsMargins(0, 0, 10, 0)
        content.addStretch()
        content.addWidget(self.arrow)

    def set_caption(self, text, description):
        self.caption = text
        self.setToolTip(description)
        self.setAccessibleName(f"{self.dimension} {description}")
        self._elide_caption()

    def _elide_caption(self):
        self.setText(self.fontMetrics().elidedText(self.caption, Qt.TextElideMode.ElideRight, max(0, self.width() - 50)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_caption()


class QueryPanel(QWidget):
    applied = Signal(object)  # AppliedQuery: 공통 조건 계약, 데이터 조회 완료 신호가 아님
    confirmed = Signal()  # 팝업 선택·날짜 초안 확정. 조회는 별도의 applied 신호만 사용한다.
    draft_changed = Signal(object)  # QuerySelection
    catalog_changed = Signal(object)  # QueryCatalog
    targets_changed = Signal(object)  # 목표 초안 변경. 조회 적용과 별개
    date_range_selected = Signal(object, object)  # start, end: 조회 적용 전에 기간 메타데이터를 전달한다.

    def __init__(self, state: QueryState, *, period=False, show_group=True, show_search=True):
        super().__init__()
        self.state = state
        self.period = period
        self.target_describer = None
        self.apply_guard = None  # 허용 시 빈 값, 차단 시 사용자에게 보여 줄 사유를 반환한다.
        self._goal_available = self._version_available = False
        self.combos: dict[str, _FilterButton] = {}
        self.filter_popup = None
        self.date_edit = None
        self.original_date_field = None
        self.range_button = None
        self._range_enabled = False
        self._applied_range = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self.card = Card()
        self.card.setProperty("outlined", True)
        self.card.body.setContentsMargins(20, 16, 20, 16)
        self.card.body.setSpacing(12)
        outer.addWidget(self.card)
        # VER5 조회 패널에는 공정·개인 설정 줄이 없다. 개인 설정 연결 전까지 숨겨 둔다.
        self.heading = QWidget()
        self.heading.hide()
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(12)
        process_label = label("Etch", 14, medium=True)
        process_label.setFixedWidth(110)
        heading.addWidget(process_label)
        self.preferences_button = button("라인 · 설비군 설정")
        self.preferences_button.setEnabled(False)
        self.preferences_button.setFixedSize(200, 36)
        self.preferences_button.setToolTip("개인별 기본 선택지 설정은 별도 작업입니다.")
        heading.addWidget(self.preferences_button)
        heading.addStretch()
        self.card.body.addWidget(self.heading)
        # 선택 중 요약은 상태 계산·접근성용으로 유지하고 VER5 화면에서는 적용 줄만 보인다.
        self.draft_summary = label("", 13, color=SECONDARY, wrap=True)
        self.draft_summary.hide()
        self.card.body.addWidget(self.draft_summary)
        self.source_label = label("", 12, color=SECONDARY, wrap=True)
        if period:
            date_field, normal_date = self._date_field("기준날짜")
            normal_date.hide()
            normal_date.deleteLater()
            self.original_date_field = date_field
            fields = [date_field]
            # 기존 기간 조회 API는 유지하고 화면에는 공통 날짜 범위 필드 하나만 둔다.
            self.start_date, self.end_date = DateEdit(self), DateEdit(self)
            for control in (self.start_date, self.end_date):
                control.hide()
                control.dateChanged.connect(lambda _day, edit=control: self._confirm_period_edit(edit))
                control.calendar_clicked.connect(lambda day, edit=control: self._confirm_period_date(edit, day))
                control.calendar_activated.connect(lambda day, edit=control: self._confirm_period_date(edit, day))
        else:
            date_field, self.date_edit = self._date_field("기준날짜")
            self.original_date_field = date_field
            self.date_edit.setObjectName("query_as_of")
            self.date_edit.setToolTip("날짜를 선택한 뒤 조회 버튼을 누르면 해당 날짜까지 저장된 가장 최근 자료로 계산합니다.")
            self.date_edit.dateChanged.connect(self._choose_date)
            self.date_edit.calendar_clicked.connect(self._confirm_date)
            self.date_edit.calendar_activated.connect(self._confirm_date)
            fields = [date_field]
        for key, caption in FILTER_CAPTIONS.items():
            if not show_group and key == "group_key":
                continue
            field = QWidget()
            layout = QVBoxLayout(field)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            title = label(caption, medium=True, color=INK)
            combo = _FilterButton(caption)
            title.setBuddy(combo)
            combo.clicked.connect(lambda checked=False, name=key: self._open_filter(name))
            self.combos[key] = combo
            layout.addWidget(title)
            layout.addWidget(combo)
            fields.append(field)
        # 조건 초기화는 아이콘 버튼. Site~설비군은 전체로, 기준 날짜는 오늘로 되돌린다.
        self.reset_button = button("", asset="ver5/reset.svg")
        query_buttons = [(self.reset_button, "조건 초기화")]
        if show_search:
            self.search_button = button("", asset="query-search.svg")
            query_buttons.append((self.search_button, "조회"))
        actions = QWidget()
        actions_body = QHBoxLayout(actions)
        actions_body.setContentsMargins(0, 0, 0, 0)
        actions_body.setSpacing(8)
        for control, caption in query_buttons:
            control.setText("")
            control.setStyleSheet("padding: 8px 0;")
            control.setProperty("soft", True)
            control.setFixedSize(38, 38)
            control.setAccessibleName(caption)
            control.setToolTip(caption)
            actions_body.addWidget(control, 0, Qt.AlignmentFlag.AlignBottom)
        actions.setFixedWidth(actions_body.sizeHint().width())
        self.query_tools = actions
        fields.append(actions)
        self.fields_row = _WrapRow(fields, 128, fields=True)
        self.card.body.addWidget(self.fields_row)
        self.goal_button = button("기준 버전 설정")
        self.goal_button.setEnabled(False)
        self.goal_button.setToolTip("목표 N 편집은 별도 작업입니다.")
        self.version_button = button(version_filter_caption(None))
        self.version_button.setEnabled(False)
        self.version_button.setToolTip("조회에 포함할 버전을 선택합니다.")
        for control in (self.goal_button, self.version_button):
            control.setFixedHeight(38)
            control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.actions_row = _WrapRow([self.goal_button, self.version_button], 150)
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(12)
        footer.addWidget(self.actions_row, 1)
        self.status = StatusBadge("조회 전", "info")
        self.status.setAccessibleName("조회 적용 상태")
        footer.addWidget(self.status)
        # 기존 버전 선택 표시를 참조하는 연결도 같은 공통 변경 상태를 사용한다.
        self.version_pending = self.status
        self.card.body.addWidget(self.footer)
        self.hint = label("날짜와 필터를 설정한 뒤 조회 버튼을 눌러 적용하세요.",
                          13, color=SECONDARY, wrap=True)
        self.card.body.addWidget(self.hint)
        self.card.body.addWidget(self.source_label)
        applied = QHBoxLayout()
        applied.setContentsMargins(0, 0, 0, 0)
        applied.setSpacing(12)
        self.applied_summary = label("", 13, color=SECONDARY, wrap=True)
        self.applied_summary.setAccessibleName("실제 적용 조건")
        self.applied_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        applied.addWidget(self.applied_summary, 1)
        outer.addLayout(applied)
        # 화면 단순화: 안내 문구·선택지 출처·적용 상태 줄은 표시하지 않는다(값은 내부·접근성용으로 유지).
        for hidden in (self.hint, self.source_label, self.applied_summary):
            hidden.hide()
        if not period:
            self.reset_button.clicked.connect(self.reset_draft)
            if show_search:
                self.search_button.clicked.connect(self.apply_draft)
        self._render()
        if period:
            self.enable_date_range(self.start_date.date().toPython(), self.end_date.date().toPython())

    def _filter_options(self, field):
        catalog = self.state.catalog
        if field in ("site_key", "group_key"):
            choices = catalog.sites if field == "site_key" else catalog.groups
            return tuple((choice.key, choice.label, None) for choice in choices)
        sites = {site.key: site.label for site in catalog.sites}
        fabs = {fab.key: fab.label for fab in catalog.fabs}
        options = [(UNASSIGNED, "미지정", None)]
        if field == "fab_key":
            options.extend((fab.key, fab.label, sites.get(fab.site_key)) for fab in self.state.available_fabs())
        else:
            lines = self.state.available_lines()
            label_counts = Counter(line.label for line in lines)
            for line in lines:
                parent = sites[line.site_key]
                if line.fab_key is not None:
                    parent += " / " + fabs[line.fab_key]
                name = f"{line.label} · {parent}" if label_counts[line.label] > 1 else line.label
                options.append((line.key, name, parent))
        return tuple(options)

    def _open_filter(self, field):
        if self.filter_popup is not None:
            self.filter_popup.reject()
        state = self.state
        expected, revision = state.draft, state.catalog_revision
        anchor = self.combos[field]
        # 선택 중인 조건만 바꾸고, 조회 버튼을 누르기 전의 결과는 유지한다.
        popup = QueryFilterPopup(FILTER_CAPTIONS[field], self._filter_options(field),
                                 selection_keys(getattr(expected, field)), parent=self, anchor=anchor,
                                 show_groups=field != "line_key",
                                 invalid_names=tuple(state.option_label(field, key)
                                                     for key in state.invalid_selections.get(field, ())))
        self.filter_popup = popup
        anchor.arrow.set_expanded(True)

        def confirm_selection():
            if (self.filter_popup is not popup or state.draft != expected
                    or state.catalog_revision != revision):
                return
            self.select(field, popup.selected_keys or None)
            self.confirmed.emit()

        def finish(_result):
            if self.filter_popup is popup:
                self.filter_popup = None
            anchor.arrow.set_expanded(False)
            popup.deleteLater()

        popup.accepted.connect(confirm_selection)
        popup.finished.connect(finish)
        popup.open()

    @staticmethod
    def _date_field(caption):
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = label(caption, medium=True, color=INK)
        control = DatePickerEdit(open_on_click=True)
        control.setProperty("queryField", True)
        control.setAccessibleName(caption)
        calendar_icon = (ASSETS / "import-ver5/fdb91.svg").as_posix()
        control.setStyleSheet(f"QDateEdit::down-arrow {{image:url({calendar_icon});width:16px;height:16px;}}")
        control.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control.setFixedHeight(38)
        control.setKeyboardTracking(False)
        title.setBuddy(control)
        layout.addWidget(title)
        layout.addWidget(control)
        field.setMinimumWidth(128)
        return field, control

    def _confirm_period_edit(self, control):
        self.sync_period_range()
        if not control.is_calendar_visible():
            self.confirmed.emit()

    def _confirm_period_date(self, control, day):
        with QSignalBlocker(control):
            control.setDate(day)
        self.sync_period_range()
        self.confirmed.emit()

    def sync_period_range(self):
        """기간 입력을 신호 없이 변경한 호출자도 공통 날짜 표시를 갱신할 수 있다."""
        if not self.period or self.range_button is None:
            return
        start, end = self.start_date.date().toPython(), self.end_date.date().toPython()
        if start <= end:
            self.range_button.set_range(start, end)
        self._render()

    def enable_date_range(self, start, end):
        if self.original_date_field is None:
            return
        if self.period:
            for control, value in ((self.start_date, start), (self.end_date, end)):
                with QSignalBlocker(control):
                    control.setDate(QDate(value.year, value.month, value.day))
        if self.range_button is None:
            from setron.ui.date_range import DateRangeButton

            self.range_button = DateRangeButton(self.original_date_field)
            self.range_button.setAccessibleName("기준날짜 기간")
            self.range_button.range_changed.connect(self._confirm_date_range)
            self.original_date_field.layout().addWidget(self.range_button)
        if not self._range_enabled and self._applied_range is None and self.state.applied is not None:
            # 처음 여는 추이 보기의 기본 기간은 이미 그린 결과와 같은 표시 메타데이터다.
            self._applied_range = (start, end)
        self.range_button.set_range(start, end, maximum=None if self.period else date.today())
        self._range_enabled = True
        if self.date_edit is not None:
            self.date_edit.hide()
        self.range_button.show()
        self.original_date_field.layout().itemAt(0).widget().setBuddy(self.range_button)
        self.original_date_field.setMinimumWidth(240)
        self.fields_row.reflow()
        self._render()

    def disable_date_range(self):
        if self.range_button is None or self.period:
            return
        if self.range_button.dialog is not None:
            self.range_button.dialog.reject()
        self.range_button.hide()
        self._range_enabled = False
        self.date_edit.show()
        self.original_date_field.layout().itemAt(0).widget().setBuddy(self.date_edit)
        self.original_date_field.setMinimumWidth(128)
        self.fields_row.reflow()
        self._render()

    def _confirm_date_range(self, start, end):
        if self.period:
            for control, value in ((self.start_date, start), (self.end_date, end)):
                with QSignalBlocker(control):
                    control.setDate(QDate(value.year, value.month, value.day))
        self.date_range_selected.emit(start, end)
        if not self.period:
            self.set_as_of(end)
        else:
            self._render()
        self.confirmed.emit()

    def _choose_date(self, value):
        if not self.date_edit.is_calendar_visible():
            self._confirm_date(value)

    def _confirm_date(self, value):
        self.set_as_of(value.toPython())
        self.confirmed.emit()

    def set_today(self):
        self.set_as_of(None)

    def set_as_of(self, value):
        previous = self.state.draft
        self.state.set_as_of(value)
        self._render()
        if previous != self.state.draft:
            self.draft_changed.emit(self.state.draft)

    def select(self, field, value):
        previous = self.state.draft
        self.state.select(field, value)
        self._render()
        if previous != self.state.draft:
            self.draft_changed.emit(self.state.draft)

    def set_draft(self, selection: QuerySelection):
        previous = self.state.draft
        self.state.set_draft(selection)
        self._render()
        if previous != selection:
            self.draft_changed.emit(selection)

    def reset_draft(self):
        self.set_draft(QuerySelection())

    def set_version_filter(self, value):
        self.select("versions", value)

    def set_target_describer(self, describer):
        self.target_describer = describer
        self._render()

    def set_goal_available(self, available):
        self._goal_available = available
        self._render()

    def set_version_available(self, available):
        self._version_available = available
        self._render()

    def _describe_targets(self, targets):
        if self.state.invalid_selections:
            return "조회 선택 다시 확인"
        try:
            return self.target_describer(targets)
        except (ValueError, KeyError):
            return "목표 기준 다시 확인 · 현재 모델·계열 정보를 다시 읽어 주세요."

    def set_target_draft(self, targets):
        previous = self.state.target_draft
        self.state.set_target_draft(targets)
        self._render()
        if previous != self.state.target_draft:
            self.targets_changed.emit(self.state.target_draft)

    def apply_draft(self):
        if self.state.invalid_selections:
            self._render()
            return None
        if self.apply_guard is not None and self.apply_guard():
            self._render()
            return None
        query = self.state.apply()
        self._applied_range = self._range_signature()
        self._render()
        self.applied.emit(query)
        return query

    def _range_signature(self):
        if self._range_enabled and self.range_button is not None:
            return self.range_button.start, self.range_button.end
        return None

    def set_catalog(self, catalog: QueryCatalog):
        if self.filter_popup is not None:
            self.filter_popup.reject()
        previous = self.state.draft
        self.state.replace_catalog(catalog)
        self._render()
        self.catalog_changed.emit(catalog)
        if previous != self.state.draft:
            self.draft_changed.emit(self.state.draft)

    def _render(self):
        self.version_button.setText(version_filter_caption(self.state.draft.versions))
        self.version_button.setAccessibleName(self.version_button.text())
        for field, combo in self.combos.items():
            selected = selection_keys(getattr(self.state.draft, field))
            options = self._filter_options(field)
            option_names = {key: name for key, name, _ in options}
            names = [option_names.get(key, self.state.option_label(field, key)) for key in selected]
            caption = "전체" if not names else names[0] if len(names) == 1 else f"{names[0]} 외 {len(names) - 1}개"
            if field in self.state.invalid_selections:
                caption = "선택 다시 확인 · " + caption
            combo.set_caption(caption, ", ".join(names) if names else caption)
            combo.setEnabled(bool(options) or bool(selected))
        if self.date_edit is not None:
            today = date.today()
            previous_block = self.date_edit.blockSignals(True)
            self.date_edit.setMaximumDate(QDate(today.year, today.month, today.day))
            shown = self.state.draft.as_of or today
            self.date_edit.setDate(QDate(shown.year, shown.month, shown.day))
            self.date_edit.blockSignals(previous_block)
        self.draft_summary.setText("선택 중 · " + self.state.describe(self.state.draft))
        if self.target_describer is not None:
            self.draft_summary.setText(self.draft_summary.text() + "\n선택 중 목표 N · " + self._describe_targets(self.state.target_draft))
        self.source_label.setText(self.state.catalog.source_label)
        applied = self.state.applied
        if applied is None:
            status, tone = "미적용", "info"
            summary = "아직 적용된 조건이 없습니다. 날짜와 필터를 설정한 뒤 조회 버튼을 눌러 주세요."
        else:
            if not self.state.applied_is_current:
                status, tone = "선택지 변경 · 재적용 필요", "warning"
            elif self.state.has_pending_changes:
                status, tone = "선택 변경 · 재적용 필요", "warning"
                if self.state.target_draft != applied.targets:
                    status = "선택·목표 변경 · 재적용 필요" if self.state.draft != applied.selection else "목표 변경 · 재적용 필요"
            else:
                status, tone = "조건 적용됨", "info"
            summary = f"적용 #{applied.revision} · {applied.description}"
            if self.target_describer is not None:
                summary += "\n적용 목표 N · " + self._describe_targets(applied.targets)
        self.status.setText(status)
        self.status.set_tone(tone)
        self.applied_summary.setText(summary)
        range_pending = (applied is not None and self._range_enabled
                         and self._range_signature() != self._applied_range)
        pending = (applied is not None and (not self.state.applied_is_current
                                           or self.state.has_pending_changes or range_pending))
        self.status.setVisible(applied is None or pending)
        if pending:
            self.status.setText("변경사항 미적용")
            self.status.set_tone("warning")
            range_hint = ""
            if range_pending:
                if self._applied_range is not None:
                    range_hint += f"\n적용 기간 · {self._applied_range[0]:%Y-%m-%d} ~ {self._applied_range[1]:%Y-%m-%d}"
                start, end = self._range_signature()
                range_hint += f"\n변경 기간 · {start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
            self.status.setToolTip(summary + "\n\n변경 중 · " + self.state.describe(self.state.draft)
                                   + range_hint + "\n조회 버튼을 누르면 반영됩니다.")
        else:
            self.status.setText("조회 전" if applied is None else status)
            self.status.setToolTip(summary)
        blocked = self.apply_guard() if self.apply_guard is not None else None
        if blocked:
            self.status.show()
            self.status.setText("조회 잠김")
            self.status.set_tone("warning")
            self.status.setToolTip(blocked)
        if self.state.invalid_selections:
            self.status.show()
            self.status.setText("선택 다시 확인")
            self.status.set_tone("warning")
            self.status.setToolTip(self.state.selection_error + "\n\n" + summary)
        if hasattr(self, "search_button"):
            self.search_button.setEnabled(not self.state.invalid_selections)
        self.goal_button.setEnabled(self._goal_available and not self.state.invalid_selections and not blocked)
        self.version_button.setEnabled(self._version_available and not self.state.invalid_selections and not blocked)
        self.status.setAccessibleDescription(self.status.toolTip())
