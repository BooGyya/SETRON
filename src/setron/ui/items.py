"""B-3 Item 매니저. 메모리 읽기 자료만 표시하며 SQL·수정·저장은 수행하지 않는다."""

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from setron.features.item.schemas import APPLICATION_LABELS, Application, ItemState
from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.item_data import BASIS_LABELS, ItemEquipmentSelection, ItemManagerData, selected_application
from setron.ui.item_distribution import ItemDistribution
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import SECONDARY
from setron.ui.versions import VersionsPage, _fit
from setron.ui.read_worker import ReadRequest


def _text(text="", size=13):
    widget = label(text, size, color=SECONDARY if size == 13 else None, wrap=True)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


class ItemsPage(QWidget):
    """메모리 또는 명시적 BackgroundRead 공급자를 사용하는 Item 조회 화면."""

    mode_changed = Signal(str)
    equipment_selected = Signal(object)
    selection_invalidated = Signal()
    page_size = 12

    def __init__(self, loader):
        """기존 Item 화면에 읽기 요청 조정자를 연결한다.

        param
        - loader: ItemManagerData 메모리 공급자 또는 워커용 BackgroundRead.
        """
        super().__init__()
        self.loader, self.data = loader, None
        self.read_request = ReadRequest(self)
        self.read_request.completed.connect(self._loaded)
        self.mode, self.page_index, self.selected_id = "list", 0, None
        self.visible_entries, self.equipment = (), ()
        self.revision, self.selected_equipment_id = 0, None
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.status = StatusBadge("Item 자료 확인 중", "info")
        self.notice = _text()
        body.addWidget(self.status)
        body.addWidget(self.notice)
        self.list_view, self.detail_view = QWidget(), QWidget()
        listing, detail = QVBoxLayout(self.list_view), QVBoxLayout(self.detail_view)
        for layout in (listing, detail):
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(20)
        card = Card()
        card.body.addWidget(label("Item 검색", 20, medium=True))
        card.body.addWidget(_text("변경 내용 또는 원본 Item 번호로 검색합니다. 같은 번호도 Item ID로 구분합니다."))
        self.search = QLineEdit()
        self.search.setAccessibleName("Item 검색")
        self.search.setPlaceholderText("변경 내용 또는 Item 번호")
        self.search.setFixedHeight(38)
        self.search.setStyleSheet("QLineEdit {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:10px;padding:8px;}")
        card.body.addWidget(self.search)
        listing.addWidget(card)
        card = Card()
        card.body.addWidget(label("Item 목록", 20, medium=True))
        self.count = _text()
        card.body.addWidget(self.count)
        self.list_model = ReadTableModel(("No / Item ID", "변경 내용", "확인된 포함 릴리즈", "출처 문서"))
        self.table = read_table(self.list_model, "Item 목록", (170, 440, 190, 280))
        self.table.clicked.connect(lambda index: self.open_row(index.row()))
        self.table.activated.connect(lambda index: self.open_row(index.row()))
        card.body.addWidget(self.table)
        self.empty = _text()
        card.body.addWidget(self.empty)
        controls = QHBoxLayout()
        self.previous, self.next, self.refresh = button("이전"), button("다음"), button("다시 불러오기")
        self.page_label = label("", 13)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        self.refresh.clicked.connect(self.reload)
        for widget in (self.previous, self.page_label, self.next):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(self.refresh)
        card.body.addLayout(controls)
        listing.addWidget(card)
        self.back = button("Item 목록으로 돌아가기")
        self.back.clicked.connect(self.back_to_list)
        detail.addWidget(self.back, alignment=Qt.AlignmentFlag.AlignLeft)
        card = Card()
        self.title, self.content = _text("", 20), _text("", 14)
        card.body.addWidget(self.title)
        card.body.addWidget(self.content)
        detail.addWidget(card)
        self.release_model, self.release_table, self.release_note = self._section(
            detail, "포함 릴리즈 · 모델 적용 범위", ("SW 계열", "버전 원문", "확인된 순서", "포함 상태", "모델 범위"),
            (180, 240, 200, 180, 280))
        self.source_model, self.source_table, self.source_note = self._section(
            detail, "Item 출처 · 원본 위치", ("대상 / 필드", "문서 · 리비전", "시트 · 셀", "입력 작업"), (180, 380, 280, 180))
        card = Card()
        card.body.addWidget(label("설비별 적용 현황", 20, medium=True))
        self.observation_note, self.summary = _text(), _text()
        card.body.addWidget(self.observation_note)
        card.body.addWidget(self.summary)
        self.state_filter = QComboBox()
        self.state_filter.setAccessibleName("Item 설비 적용 상태")
        self.state_filter.setProperty("queryField", True)
        self.state_filter.setFixedHeight(38)
        self.state_filter.addItem("전체 상태", None)
        for state, name in APPLICATION_LABELS.items():
            self.state_filter.addItem(name, state)
        self.site_filter, self.distribution_view = QComboBox(), QComboBox()
        for widget, name in ((self.site_filter, "Item 적용 Site"), (self.distribution_view, "Item 적용 보기")):
            widget.setAccessibleName(name)
            widget.setProperty("queryField", True)
            widget.setFixedHeight(38)
            widget.setMinimumWidth(0)
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.site_filter.addItem("전체 Site", None)
        self.distribution_view.addItem("설비 목록", "list")
        self.distribution_view.addItem("Site · 설비 히트맵", "heatmap")
        card.body.addWidget(_WrapRow([self.site_filter, self.state_filter, self.distribution_view], 200, fields=True))
        self.distribution = ItemDistribution()
        card.body.addWidget(self.distribution)
        self.distribution.scope_selected.connect(self.select_distribution_scope)
        self.distribution.equipment_selected.connect(self.open_equipment)
        self.equipment_model = ReadTableModel(("설비 / ID", "Site", "모델", "적용 상태", "PM별 버전 · 판정 근거"))
        self.equipment_table = read_table(self.equipment_model, "Item 설비별 적용 현황", (200, 120, 180, 120, 470))
        self.equipment_table.clicked.connect(lambda index: self.open_equipment_row(index.row()))
        self.equipment_table.activated.connect(lambda index: self.open_equipment_row(index.row()))
        card.body.addWidget(self.equipment_table)
        self.equipment_empty = _text()
        self.scope_count = _text()
        card.body.addWidget(self.scope_count)
        card.body.addWidget(self.equipment_empty)
        detail.addWidget(card)
        detail.addWidget(_text("설비 행 또는 타일 선택 → 기존 설비 상세와 Item 근거 · 복귀 시 선택 범위 유지\n"
                               "읽기 전용 · 원본 문서/셀 열기와 위험 규칙 근거는 미연결입니다."))
        body.addWidget(self.list_view)
        body.addWidget(self.detail_view)
        self.search.textChanged.connect(self._filter_changed)
        self.state_filter.currentIndexChanged.connect(self._scope_changed)
        self.site_filter.currentIndexChanged.connect(self._scope_changed)
        self.distribution_view.currentIndexChanged.connect(self.render_equipment)
        self.reload()

    def _section(self, layout, title, headings, widths):
        card = Card()
        card.body.addWidget(label(title, 20, medium=True))
        note = _text()
        model = ReadTableModel(headings)
        table = read_table(model, title, widths)
        card.body.addWidget(note)
        card.body.addWidget(table)
        layout.addWidget(card)
        return model, table, note

    def _set_mode(self, mode):
        self.mode = mode
        for view, visible in ((self.list_view, mode == "list"), (self.detail_view, mode == "detail")):
            view.setVisible(visible)
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred if visible else QSizePolicy.Policy.Ignored)
        self.mode_changed.emit(mode)

    def reload(self):
        """이전 상세 요청을 무효화하고 중복 실행 없이 자료를 요청한다."""
        if self.read_request.busy:
            return
        self._invalidate_selection()
        self.selected_id, self.equipment = None, ()
        self.title.clear()
        self.content.clear()
        for model in (self.release_model, self.source_model, self.equipment_model):
            model.set_rows(())
        self.data = None
        self.search.setEnabled(False)
        self._filter_changed()
        self.refresh.setEnabled(False)
        self.status.setText("Item 자료 불러오는 중")
        self.status.set_tone("info")
        self.notice.setText("조회 중입니다. 완료될 때까지 기다려 주세요.")
        self.empty.setText("Item 자료 불러오는 중")
        self.read_request.start(self.loader)

    def _loaded(self, data, error):
        """현재 요청 결과에 기존 목록·오류 표시를 적용한다.

        param
        - data: 조회 결과.
        - error: 공급자 실패 또는 None.
        """
        self.refresh.setEnabled(True)
        if error is not None or not isinstance(data, ItemManagerData):
            self.data = None
            self.status.setText("Item 자료 불러오기 실패")
            self.status.set_tone("danger")
            self.notice.setText("자료를 불러오지 못했습니다. 다시 불러오기로 재시도해 주세요. 이전 상세는 표시하지 않습니다.")
        else:
            self.data = data
            self.status.setText("Item 매니저 · 읽기 전용")
            self.status.set_tone("info")
            self.notice.setText(data.versions.source_label)
        self.search.setEnabled(self.data is not None)
        self._filter_changed()

    def hideEvent(self, event):
        """화면 이탈 시 진행 중 응답을 무효화한다.

        param
        - event: Qt 숨김 이벤트.
        """
        if self.read_request.busy:
            self.read_request.cancel()
            self.refresh.setEnabled(True)
            self.status.setText("조회 취소")
            self.notice.setText("다시 불러오기를 눌러 조회해 주세요.")
            self.empty.setText("조회 취소 · 다시 불러오기 가능")
        super().hideEvent(event)

    def _filter_changed(self, *_):
        self._invalidate_selection()
        self.page_index, self.selected_id = 0, None
        self._set_mode("list")
        self.render_list()

    def render_list(self):
        found = self.data.search(self.search.text()) if self.data else ()
        pages = max(1, (len(found) + self.page_size - 1) // self.page_size)
        self.page_index = max(0, min(self.page_index, pages - 1))
        self.visible_entries = found[self.page_index * self.page_size:(self.page_index + 1) * self.page_size]
        rows = []
        for entry in self.visible_entries:
            item = entry.item
            count = sum(r.item_id == item.item_id and r.state is ItemState.APPLIED for r in self.data.versions.version_items)
            docs = tuple(dict.fromkeys(s.document for s in entry.sources if s.document))
            rows.append((f"{item.item_no if item.item_no is not None else '번호 미확인'} · ID {item.item_id}",
                         item.content, f"확인된 {count}건", "\n".join(docs) or "출처 미확인"))
        self.list_model.set_rows(rows, tuple(e.item.item_id for e in self.visible_entries))
        _fit(self.table)
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.empty.setText("자료 불러오기 실패 · 재시도해 주세요." if self.data is None else
                           "검색 결과가 없습니다." if self.search.text().strip() else "Item 자료 없음 또는 미연결 · 미포함으로 판정하지 않습니다.")
        self.count.setText(f"검색 결과 {len(found)}건 · 행 선택 또는 Enter로 상세 보기 · 포함 건수는 저장된 확인 행 기준")
        self.page_label.setText(f"{self.page_index + 1} / {pages}")
        self.previous.setEnabled(self.page_index > 0)
        self.next.setEnabled(self.page_index + 1 < pages)

    def change_page(self, delta):
        self.page_index += delta
        self.selected_id = None
        self.render_list()

    def open_row(self, row):
        if self.mode != "list" or not 0 <= row < len(self.visible_entries):
            return False
        entry = self.visible_entries[row]
        self._invalidate_selection()
        item = entry.item
        self.selected_id = item.item_id
        self.title.setText(f"Item {item.item_no if item.item_no is not None else '번호 미확인'} · ID {item.item_id}")
        self.content.setText(item.content)
        catalog = self.data.catalog()
        names = {p.family_id: p.family_name for p in self.data.versions.pairs}
        releases = catalog.item_versions(item.item_id)
        self.release_model.set_rows(tuple((names[r.release.family_id], r.release.version_raw,
            self.data.versions.order_label(r.release),
            "미확인 · 저장 행 없음" if r.source is None else
            {ItemState.APPLIED: "포함", ItemState.NOT_APPLIED: "미포함", ItemState.UNKNOWN: "미확인"}[r.state],
            self.data.scope_label(r.source)) for r in releases))
        self.release_note.setText("계열별 확인된 순서 · 미확인 순서는 마지막. 이전 Item을 최신 버전에 자동 상속하지 않습니다." if releases else
                                  "연결된 릴리즈가 없습니다. 포함 여부와 적용 범위를 확인할 수 없습니다.")
        self.release_table.setVisible(bool(releases))
        sources = []
        VersionsPage._append_sources(sources, f"Item {item.item_id}", entry.sources)
        self.source_model.set_rows(sources)
        self.source_note.setText("전달받은 원본 출처입니다. 관리값 수정은 제공하지 않습니다.")
        # 연결이 전혀 없는 Item은 다른 계열의 대상 외라는 판정을 확대하지 않는다.
        connected = self.data.observations_connected and bool(releases)
        summary = catalog.summarize(item, self.data.records) if connected else None
        self.equipment = summary.equipment if summary else ()
        self.observation_note.setText((f"관측 기준일 {self.data.base_date.isoformat()} · 전체 관측 범위 · 공통 조회 조건과 독립\n" if self.data.observations_connected else "")
                                     + self.data.observation_label)
        self.summary.setText(" · ".join(f"{APPLICATION_LABELS[s]} {n}대" for s, n in summary.counts().items())
                             + f" · HPA 제외 {summary.hpa_excluded}대" if summary else "적용 현황 미확인 · 릴리즈 연결 또는 설비 관측 자료가 필요합니다.")
        with QSignalBlocker(self.state_filter):
            self.state_filter.setCurrentIndex(0)
        with QSignalBlocker(self.site_filter):
            self.site_filter.clear()
            self.site_filter.addItem("전체 Site", None)
            for site in sorted({e.record.site for e in self.equipment}):
                self.site_filter.addItem(site, site)
        self.state_filter.setEnabled(summary is not None)
        self.site_filter.setEnabled(summary is not None)
        self.render_equipment()
        self._set_mode("detail")
        for table in (self.release_table, self.source_table, self.equipment_table):
            _fit(table)
        self.back.setFocus()
        return True

    def render_equipment(self, *_):
        state = self._application_filter()
        site = self.site_filter.currentData()
        rows = [e for e in self.equipment if (state is None or e.application == state)
                and (site is None or e.record.site == site)]
        heatmap = self.distribution_view.currentData() == "heatmap"
        self.distribution.set_results(self.equipment if heatmap else (), rows if heatmap else (), self.selected_equipment_id)
        self.distribution.set_scope(site, state)
        self.distribution.setVisible(heatmap)
        self.equipment_model.set_rows(tuple((f"{e.record.equipment_code} · ID {e.record.equipment_id}",
            e.record.site, e.record.model_name, e.label,
            "\n".join(f"{p.pm.pm_name or '일반 SW'} · 계열 {p.pm.family_id} · {p.pm.version_raw or '버전 미확인'} · "
                      f"{APPLICATION_LABELS[p.application]} ({BASIS_LABELS[p.basis]})" for p in e.pms)
            or "PM/SW 관측 없음 · 업무 규칙상 대상 외") for e in rows), tuple(e.record.equipment_id for e in rows))
        self.scope_count.setText(f"표시 범위: {site if site is not None else '전체 Site'} · "
                                 f"{APPLICATION_LABELS[state] if state else '전체 상태'} · {len(rows)}대 / 전체 {len(self.equipment)}대")
        if self.selected_equipment_id in self.equipment_model.ids:
            self.equipment_table.setCurrentIndex(self.equipment_model.index(self.equipment_model.ids.index(self.selected_equipment_id), 0))
        self.equipment_table.setVisible(bool(rows) and not heatmap)
        self.equipment_empty.setVisible(not rows)
        self.equipment_empty.setText("표시할 설비가 없습니다. 관측 자료 연결 여부와 선택 상태를 확인해 주세요.")
        _fit(self.equipment_table)

    def _invalidate_selection(self):
        self.revision += 1
        self.selected_equipment_id = None
        self.selection_invalidated.emit()

    def _application_filter(self):
        value = self.state_filter.currentData()
        return Application(value) if value is not None else None

    def _scope_changed(self, *_):
        self._invalidate_selection()
        self.render_equipment()

    def select_distribution_scope(self, site, state):
        with QSignalBlocker(self.site_filter), QSignalBlocker(self.state_filter):
            self.site_filter.setCurrentIndex(self.site_filter.findData(site))
            self.state_filter.setCurrentIndex(self.state_filter.findData(state))
        self._scope_changed()

    def is_current_selection(self, request):
        return (isinstance(request, ItemEquipmentSelection) and self.mode == "detail"
                and request.data is self.data and request.revision == self.revision
                and request.item_id == self.selected_id and request.site == self.site_filter.currentData()
                and request.equipment_id == self.selected_equipment_id
                and request.state == self._application_filter() and selected_application(request) is not None)

    def open_equipment_row(self, row):
        if 0 <= row < len(self.equipment_model.ids):
            return self.open_equipment(self.equipment_model.ids[row])
        return False

    def open_equipment(self, equipment_id):
        if self.mode != "detail" or self.data is None:
            return False
        request = ItemEquipmentSelection(self.data, self.revision, self.selected_id, equipment_id,
                                         self.site_filter.currentData(), self._application_filter())
        if selected_application(request) is None:
            return False
        self.revision += 1
        self.selected_equipment_id = equipment_id
        request = ItemEquipmentSelection(self.data, self.revision, self.selected_id, equipment_id,
                                         self.site_filter.currentData(), self._application_filter())
        self.distribution.select_equipment(equipment_id)
        if equipment_id in self.equipment_model.ids:
            self.equipment_table.setCurrentIndex(self.equipment_model.index(self.equipment_model.ids.index(equipment_id), 0))
        self.equipment_selected.emit(request)
        return True

    def back_to_list(self):
        self._invalidate_selection()
        self._set_mode("list")
        if self.selected_id in self.list_model.ids:
            self.table.setCurrentIndex(self.list_model.index(self.list_model.ids.index(self.selected_id), 0))
            self.table.setFocus()
