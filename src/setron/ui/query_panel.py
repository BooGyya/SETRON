"""공통 조회 패널. 날짜와 필터 선택이 바뀌면 조회를 요청한다."""

from collections import Counter
from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.components import Card, DisclosureArrow, StatusBadge, button, label
from setron.ui.calendar_widgets import DateEdit
from setron.ui.query_filter_popup import QueryFilterPopup
from setron.ui.query_state import UNASSIGNED, QueryCatalog, QuerySelection, QueryState, selection_keys, version_filter_caption
from setron.ui.theme import ASSETS, INK, LINE, PRIMARY, SECONDARY, SOFT, font


FILTER_CAPTIONS = {"site_key": "Site", "fab_key": "Fab", "line_key": "Line", "group_key": "설비군"}


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

    def _arrange(self, columns):
        if self.columns == columns:
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

    def reflow(self, width=None):
        width = self.width() if width is None else width
        columns = max(1, min(len(self.widgets), (width + 12) // (self.minimum_width + 12)))
        if self.fields:
            needed = sum(max(w.minimumWidth(), self.minimum_width) if w.maximumWidth() >= 10000 else w.maximumWidth()
                         for w in self.widgets) + 12 * (len(self.widgets) - 1)
            columns = len(self.widgets) if width >= needed else min(3, columns)
        self._arrange(columns)

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
    confirmed = Signal()  # 유효한 필터 변경 또는 날짜 확정
    draft_changed = Signal(object)  # QuerySelection
    catalog_changed = Signal(object)  # QueryCatalog
    targets_changed = Signal(object)  # 목표 초안 변경. 조회 적용과 별개
    date_range_selected = Signal(object, object)  # start, end: 조회 적용 전에 기간 메타데이터를 전달한다.

    def __init__(self, state: QueryState, *, period=False, show_group=True, show_search=True):
        super().__init__()
        self.state = state
        self.period = period
        self.target_describer = None
        self.combos: dict[str, _FilterButton] = {}
        self.filter_popup = None
        self.date_edit = None
        self.original_date_field = None
        self.range_button = None
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
            date_field, normal_date = self._date_field("기준 날짜")
            normal_date.hide()
            normal_date.deleteLater()
            self.original_date_field = date_field
            fields = [date_field]
            # 기존 기간 조회 API는 유지하고 화면에는 공통 날짜 범위 필드 하나만 둔다.
            self.start_date, self.end_date = DateEdit(self), DateEdit(self)
            for control in (self.start_date, self.end_date):
                control.hide()
                control.dateChanged.connect(lambda _day, edit=control: self._confirm_period_edit(edit))
                control.calendarWidget().clicked.connect(lambda day, edit=control: self._confirm_period_date(edit, day))
                control.calendarWidget().activated.connect(lambda day, edit=control: self._confirm_period_date(edit, day))
        else:
            date_field, self.date_edit = self._date_field("기준 날짜")
            self.original_date_field = date_field
            self.date_edit.setObjectName("query_as_of")
            self.date_edit.setToolTip("클릭하여 날짜를 선택합니다. 선택한 날짜까지 저장된 관측 중 가장 최근 자료로 계산합니다.")
            self.date_edit.dateChanged.connect(self._choose_date)
            self.date_edit.calendarWidget().clicked.connect(self._confirm_date)
            self.date_edit.calendarWidget().activated.connect(self._confirm_date)
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
        self.card.body.addWidget(self.actions_row)
        self.version_pending = label("버전 선택 변경 · 다음 조회에 반영", 12, color=SECONDARY)
        self.card.body.addWidget(self.version_pending)
        self.hint = label("날짜와 필터를 변경하면 바로 조회합니다.",
                          13, color=SECONDARY, wrap=True)
        self.card.body.addWidget(self.hint)
        self.card.body.addWidget(self.source_label)
        applied = QHBoxLayout()
        applied.setContentsMargins(0, 0, 0, 0)
        applied.setSpacing(12)
        self.status = StatusBadge("미적용", "info")
        applied.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        self.applied_summary = label("", 13, color=SECONDARY, wrap=True)
        self.applied_summary.setAccessibleName("실제 적용 조건")
        self.applied_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        applied.addWidget(self.applied_summary, 1)
        outer.addLayout(applied)
        # 화면 단순화: 안내 문구·선택지 출처·적용 상태 줄은 표시하지 않는다(값은 내부·접근성용으로 유지).
        for hidden in (self.hint, self.source_label, self.status, self.applied_summary):
            hidden.hide()
        if not period:
            self.confirmed.connect(self.apply_draft)
            self.reset_button.clicked.connect(self._reset_and_apply)
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
        # 리플레이가 조회 중 카드를 잠가도 열린 팝업에서는 선택을 이어갈 수 있다.
        popup = QueryFilterPopup(FILTER_CAPTIONS[field], self._filter_options(field),
                                 selection_keys(getattr(expected, field)), parent=self, anchor=anchor,
                                 show_groups=field != "line_key")
        self.filter_popup = popup
        anchor.arrow.set_expanded(True)

        def change(keys):
            nonlocal expected
            if state.draft != expected or state.catalog_revision != revision:
                return
            self.select(field, keys or None)
            if state.draft != expected:
                expected = state.draft
                self.confirmed.emit()

        def finish(_result):
            self.filter_popup = None
            anchor.arrow.set_expanded(False)
            popup.deleteLater()

        popup.selection_changed.connect(change)
        popup.finished.connect(finish)
        popup.open()

    @staticmethod
    def _date_field(caption):
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = label(caption, medium=True, color=INK)
        control = DateEdit(open_on_click=True)
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
        if not control.calendarWidget().isVisible():
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
            self.range_button.setAccessibleName("기준 날짜 기간")
            self.range_button.range_changed.connect(self._confirm_date_range)
            self.original_date_field.layout().addWidget(self.range_button)
        self.range_button.set_range(start, end, maximum=None if self.period else date.today())
        if self.date_edit is not None:
            self.date_edit.hide()
        self.range_button.show()
        self.original_date_field.layout().itemAt(0).widget().setBuddy(self.range_button)
        self.original_date_field.setMinimumWidth(240)
        self.fields_row.reflow()

    def disable_date_range(self):
        if self.range_button is None or self.period:
            return
        if self.range_button.dialog is not None:
            self.range_button.dialog.reject()
        self.range_button.hide()
        self.date_edit.show()
        self.original_date_field.layout().itemAt(0).widget().setBuddy(self.date_edit)
        self.original_date_field.setMinimumWidth(128)
        self.fields_row.reflow()

    def _confirm_date_range(self, start, end):
        if self.period:
            for control, value in ((self.start_date, start), (self.end_date, end)):
                with QSignalBlocker(control):
                    control.setDate(QDate(value.year, value.month, value.day))
        self.date_range_selected.emit(start, end)
        if not self.period:
            self.set_as_of(end)
        self.confirmed.emit()

    def _choose_date(self, value):
        if not self.date_edit.calendarWidget().isVisible():
            self._confirm_date(value)

    def _confirm_date(self, value):
        self.set_as_of(value.toPython())
        if self.state.has_pending_changes:
            self.apply_draft()

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

    def _reset_and_apply(self):
        self.reset_draft()
        self.apply_draft()

    def set_version_filter(self, value):
        self.set_draft(replace(self.state.draft, versions=value))

    def set_target_describer(self, describer):
        self.target_describer = describer
        self._render()

    def set_target_draft(self, targets):
        previous = self.state.target_draft
        self.state.set_target_draft(targets)
        self._render()
        if previous != self.state.target_draft:
            self.targets_changed.emit(self.state.target_draft)

    def apply_draft(self):
        query = self.state.apply()
        self._render()
        self.applied.emit(query)
        return query

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
        applied_versions = self.state.applied.selection.versions if self.state.applied is not None else None
        self.version_pending.setVisible(self.state.draft.versions != applied_versions)
        for field, combo in self.combos.items():
            selected = selection_keys(getattr(self.state.draft, field))
            options = self._filter_options(field)
            names = [name for key, name, _ in options if key in selected]
            caption = "전체" if not names else names[0] if len(names) == 1 else f"{names[0]} 외 {len(names) - 1}개"
            combo.set_caption(caption, ", ".join(names) if names else caption)
            combo.setEnabled(bool(options))
        if self.date_edit is not None:
            today = date.today()
            previous_block = self.date_edit.blockSignals(True)
            self.date_edit.setMaximumDate(QDate(today.year, today.month, today.day))
            shown = self.state.draft.as_of or today
            self.date_edit.setDate(QDate(shown.year, shown.month, shown.day))
            self.date_edit.blockSignals(previous_block)
        self.draft_summary.setText("선택 중 · " + self.state.describe(self.state.draft))
        if self.target_describer is not None:
            self.draft_summary.setText(self.draft_summary.text() + "\n선택 중 목표 N · " + self.target_describer(self.state.target_draft))
        self.source_label.setText(self.state.catalog.source_label)
        applied = self.state.applied
        if applied is None:
            status, tone = "미적용", "info"
            summary = "아직 적용된 조건이 없습니다. 날짜나 필터를 선택해 주세요."
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
                summary += "\n적용 목표 N · " + self.target_describer(applied.targets)
        self.status.setText(status)
        self.status.set_tone(tone)
        self.applied_summary.setText(summary)
