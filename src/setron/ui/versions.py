"""B-1 릴리즈 목록·상세. 공통 Qt 위젯과 버전/Item 읽기 서비스를 재사용한다."""

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from setron.ui.components import Card, StatusBadge, button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import SECONDARY
from setron.ui.version_data import VersionCatalog
from setron.ui.version_edit_data import EditDraft
from setron.ui.version_edit_dialog import VersionEditDialog
from setron.ui.read_worker import ReadRequest
from setron.ui.source_notice import source_description


def _field(title, widget):
    box = QWidget()
    body = QVBoxLayout(box)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(8)
    body.addWidget(label(title, 12, medium=True, color=SECONDARY))
    body.addWidget(widget)
    return box


def _fit(table):
    table.resizeRowsToContents()
    height = sum(table.rowHeight(i) for i in range(table.model().rowCount()))
    table.setMinimumHeight(140)
    table.setFixedHeight(max(140, min(560, height + table.horizontalHeader().height() + 28)))


class VersionsPage(QWidget):
    """메모리 또는 명시적 BackgroundRead 공급자를 사용하는 릴리즈 조회 화면."""

    mode_changed = Signal(str)
    page_size = 12

    def __init__(self, loader):
        """릴리즈 조회와 저장 미연결 편집 진입점을 구성한다.

        param
        - loader: VersionCatalog 메모리 공급자 또는 워커용 BackgroundRead.
        """
        super().__init__()
        self.loader, self.catalog = loader, None
        self.read_request = ReadRequest(self)
        self.read_request.completed.connect(self._loaded)
        self.mode, self.page_index, self.selected_id = "list", 0, None
        self.visible_entries, self.filtered_entries = (), ()
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        self.status = StatusBadge("릴리즈 자료 확인 중", "info")
        body.addWidget(self.status)
        self.notice = label("", 13, color=SECONDARY, wrap=True)
        self.notice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(self.notice)
        self.list_view = QWidget()
        listing = QVBoxLayout(self.list_view)
        listing.setContentsMargins(0, 0, 0, 0)
        listing.setSpacing(20)
        filters = Card()
        filters.body.addWidget(label("모델 · SW 계열 선택", 20, medium=True))
        filters.body.addWidget(label("릴리즈 문서를 조회합니다. 설비 조회 조건과 목표 N은 바뀌지 않습니다.",
                                     13, color=SECONDARY, wrap=True))
        self.model_filter, self.family_filter = QComboBox(), QComboBox()
        for widget, name in ((self.model_filter, "릴리즈 모델 선택"), (self.family_filter, "릴리즈 SW 계열 선택")):
            widget.setAccessibleName(name)
            widget.setProperty("queryField", True)
            widget.setFixedHeight(38)
            widget.setMinimumWidth(0)
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(12)
        self.search = QLineEdit()
        self.search.setAccessibleName("릴리즈 검색")
        self.search.setPlaceholderText("버전 원문 또는 문서명 검색")
        self.search.setFixedHeight(38)
        self.search.setStyleSheet("QLineEdit {background:#f7f9fc;border:1px solid #e5eaf3;border-radius:10px;padding:8px;}")
        self.order = QComboBox()
        self.order.setAccessibleName("릴리즈 정렬")
        self.order.setProperty("queryField", True)
        self.order.setFixedHeight(38)
        self.order.addItem("확인된 순서 · 내림차순", True)
        self.order.addItem("확인된 순서 · 오름차순", False)
        filters.body.addWidget(_WrapRow([_field("모델", self.model_filter), _field("SW 계열", self.family_filter),
                                        _field("검색", self.search), _field("정렬", self.order)], 230, fields=True))
        listing.addWidget(filters)
        self.list_card = Card()
        self.list_card.body.addWidget(label("릴리즈 버전 목록", 20, medium=True))
        self.count = label("", 13, color=SECONDARY, wrap=True)
        self.list_card.body.addWidget(self.count)
        self.list_model = ReadTableModel(("버전 원문", "출시일", "확인된 순서", "포함 Item", "출처 문서"))
        self.table = read_table(self.list_model, "릴리즈 버전 목록", (260, 125, 210, 190, 330))
        self.table.clicked.connect(lambda index: self.open_row(index.row()))
        self.table.activated.connect(lambda index: self.open_row(index.row()))
        self.list_card.body.addWidget(self.table)
        self.empty = label("", color=SECONDARY, wrap=True)
        self.list_card.body.addWidget(self.empty)
        controls = QHBoxLayout()
        self.previous, self.next = button("이전"), button("다음")
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        self.page_label = label("", 13, numeric=True)
        self.refresh = button("다시 불러오기")
        self.refresh.clicked.connect(self.reload)
        controls.addWidget(self.previous)
        controls.addWidget(self.page_label)
        controls.addWidget(self.next)
        controls.addStretch()
        controls.addWidget(self.refresh)
        self.list_card.body.addLayout(controls)
        listing.addWidget(self.list_card)
        body.addWidget(self.list_view)

        self.detail_view = QWidget()
        detail = QVBoxLayout(self.detail_view)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(20)
        self.back = button("릴리즈 목록으로 돌아가기")
        self.back.clicked.connect(self.back_to_list)
        detail.addWidget(self.back, alignment=Qt.AlignmentFlag.AlignLeft)
        self.overview = Card()
        self.detail_title = label("", 20, medium=True, wrap=True)
        self.detail_title.setMinimumWidth(0)
        self.detail_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_context = label("", 13, color=SECONDARY, wrap=True)
        self.detail_context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.overview.body.addWidget(self.detail_title)
        self.overview.body.addWidget(self.detail_context)
        self.info_model = ReadTableModel(("항목", "확인된 값"))
        self.info_table = read_table(self.info_model, "릴리즈 기본 정보", (160, 360))
        self.overview.body.addWidget(self.info_table)
        detail.addWidget(self.overview)
        item_card = Card()
        item_card.body.addWidget(label("포함 Item · 모델 적용 범위", 20, medium=True))
        self.item_note = label("", 13, color=SECONDARY, wrap=True)
        item_card.body.addWidget(self.item_note)
        self.item_model = ReadTableModel(("No / Item", "변경 내용", "릴리즈 포함", "모델 적용 범위", "선택 모델 판정"))
        self.item_table = read_table(self.item_model, "릴리즈 Item 목록", (150, 370, 190, 220, 185))
        item_card.body.addWidget(self.item_table)
        self.item_empty = label("", color=SECONDARY, wrap=True)
        item_card.body.addWidget(self.item_empty)
        detail.addWidget(item_card)
        source_card = Card()
        source_card.body.addWidget(label("출처 · 원본 위치", 20, medium=True))
        source_card.body.addWidget(label("아래는 전달받은 출처 정보입니다. 원본 문서·셀 열기는 아직 연결되지 않았습니다.",
                                         13, color=SECONDARY, wrap=True))
        self.source_model = ReadTableModel(("대상 / 필드", "문서 · 리비전", "시트 · 셀", "입력 작업"))
        self.source_table = read_table(self.source_model, "릴리즈와 Item 출처", (220, 430, 280, 185))
        source_card.body.addWidget(self.source_table)
        detail.addWidget(source_card)
        self.edit_button = button("릴리즈 수정")
        self.edit_button.clicked.connect(self.open_editor)
        detail.addWidget(self.edit_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.editor = None
        self.read_only = label("편집 항목 확인 대기 · 수정 화면의 공통 흐름만 준비되었으며 실제 저장은 연결되지 않았습니다.",
                               13, color=SECONDARY, wrap=True)
        detail.addWidget(self.read_only)
        body.addWidget(self.detail_view)
        self.model_filter.currentIndexChanged.connect(self._model_changed)
        self.family_filter.currentIndexChanged.connect(self._filter_changed)
        self.search.textChanged.connect(self._filter_changed)
        self.order.currentIndexChanged.connect(self._filter_changed)
        self._set_mode("list")
        self.reload()

    def _set_mode(self, mode):
        self.mode = mode
        for view, visible in ((self.list_view, mode == "list"), (self.detail_view, mode == "detail")):
            view.setVisible(visible)
            view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred if visible else QSizePolicy.Policy.Ignored)
        self.mode_changed.emit(mode)

    def reload(self):
        """이전 상세를 비우고 중복 실행 없이 자료를 요청한다."""
        if self.read_request.busy:
            return
        self._previous_filters = self.model_filter.currentData(), self.family_filter.currentData()
        self.selected_id = None
        self._set_mode("list")
        self.info_model.set_rows(())
        self.item_model.set_rows(())
        self.source_model.set_rows(())
        self.catalog = None
        self.render_list()
        self.refresh.setEnabled(False)
        self.status.setText("릴리즈 자료 불러오는 중")
        self.status.set_tone("info")
        self.notice.setText("조회 중입니다. 완료될 때까지 기다려 주세요.")
        self.empty.setText("릴리즈 자료 불러오는 중")
        self.count.setText("조회 중 · 결과 건수는 완료 후 표시합니다.")
        self.read_request.start(self.loader)

    def _loaded(self, catalog, error):
        """현재 요청 결과에 기존 목록·오류 표시를 적용한다.

        param
        - catalog: 조회 결과.
        - error: 공급자 실패 또는 None.
        """
        old_model, old_family = self._previous_filters
        self.refresh.setEnabled(True)
        if error is not None or not isinstance(catalog, VersionCatalog):
            self.catalog = None
            self.status.setText("릴리즈 자료 불러오기 실패")
            self.status.set_tone("danger")
            self.notice.setText("자료를 불러오지 못했습니다. 다시 불러오기를 눌러 재시도해 주세요. 이전 상세는 표시하지 않습니다.")
        else:
            self.catalog = catalog
            self.status.setText("릴리즈 목록 · 읽기 전용")
            self.status.set_tone("info")
            self.notice.setText(source_description(catalog.source_label))
        with QSignalBlocker(self.model_filter):
            self.model_filter.clear()
            models = {} if self.catalog is None else {p.model_id: p.model_name for p in self.catalog.pairs}
            for mid, name in models.items():
                self.model_filter.addItem(name, mid)
            index = self.model_filter.findData(old_model)
            self.model_filter.setCurrentIndex(index if index >= 0 else 0 if models else -1)
        self._model_changed(preferred_family=old_family)

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
            self.count.setText("조회 취소 · 결과 미확인")
        super().hideEvent(event)

    def _model_changed(self, *_args, preferred_family=None):
        with QSignalBlocker(self.family_filter):
            self.family_filter.clear()
            if self.catalog:
                for pair in self.catalog.pairs:
                    if pair.model_id == self.model_filter.currentData():
                        self.family_filter.addItem(pair.family_name, pair.family_id)
            index = self.family_filter.findData(preferred_family)
            if index >= 0:
                self.family_filter.setCurrentIndex(index)
        self._filter_changed()

    def _filter_changed(self, *_):
        self.page_index, self.selected_id = 0, None
        self._set_mode("list")
        self.render_list()

    def render_list(self):
        """현재 목록을 표시하며 조회 실패를 정상 0건과 구분한다."""
        catalog = self.catalog
        self.filtered_entries = () if catalog is None else catalog.list_releases(
            self.model_filter.currentData(), self.family_filter.currentData(), self.search.text(), descending=self.order.currentData())
        pages = max(1, (len(self.filtered_entries) + self.page_size - 1) // self.page_size)
        self.page_index = max(0, min(self.page_index, pages - 1))
        start = self.page_index * self.page_size
        self.visible_entries = self.filtered_entries[start:start + self.page_size]
        rows = []
        for e in self.visible_entries:
            r = e.release
            included = catalog.included_count(r.release_version_id)
            item_text = f"{included}건" if e.items_verified else f"확인된 {included}건 · 매핑 미완료"
            documents = tuple(dict.fromkeys(s.document for s in e.sources if s.document))
            rows.append((r.version_raw, r.released_at.isoformat() if r.released_at else "미확인",
                         catalog.order_label(r), item_text, "\n".join(documents) or "출처 미확인"))
        self.list_model.set_rows(rows, tuple(e.release.release_version_id for e in self.visible_entries))
        _fit(self.table)
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.empty.setText("다시 불러오기로 재시도해 주세요." if catalog is None else
                           "검색 결과가 없습니다. 검색어를 지우거나 모델·SW 계열을 바꿔 주세요." if self.search.text().strip() else
                           "선택한 모델·SW 계열에 등록된 릴리즈가 없습니다.")
        self.count.setText(f"검색 결과 {len(self.filtered_entries)}건 · 행을 선택하거나 Enter로 상세 보기\n"
                           "확인된 순서 기준 정렬 · 순서 미확인은 마지막 · 중복 순서는 선후 미확인")
        self.page_label.setText(f"{self.page_index + 1} / {pages}")
        if catalog is None:
            self.count.setText("조회 결과 미확인")
        self.previous.setEnabled(self.page_index > 0)
        self.next.setEnabled(self.page_index + 1 < pages)
        for widget in (self.model_filter, self.family_filter, self.search, self.order):
            widget.setEnabled(catalog is not None and bool(catalog.pairs))

    def change_page(self, delta):
        self.page_index += delta
        self.selected_id = None
        self.render_list()

    def open_row(self, row):
        if self.mode != "list" or not 0 <= row < len(self.visible_entries):
            return False
        entry = self.visible_entries[row]
        self.selected_id = entry.release.release_version_id
        r = entry.release
        self.detail_title.setText(r.version_raw)
        self.detail_context.setText(self.model_filter.currentText() + " · " + self.family_filter.currentText())
        names = {p.model_id: p.model_name for p in self.catalog.pairs}
        self.info_model.set_rows((
            ("릴리즈 ID", str(r.release_version_id)),
            ("출시일", r.released_at.isoformat() if r.released_at else "미확인"),
            ("확인된 순서", self.catalog.order_label(r)),
            ("연결 모델", ", ".join(names[mid] for mid in entry.model_ids)),
            ("Item 매핑", "확인 완료" if entry.items_verified else "미확인 · 확인된 항목만 표시"),
        ))
        item_rows = self.catalog.item_rows(r.release_version_id, self.model_filter.currentData())
        self.item_model.set_rows(tuple((f"{i.item.item_no if i.item.item_no is not None else '번호 미확인'} · ID {i.item.item_id}",
                                       i.item.content, state, scope, application) for i, state, scope, application in item_rows))
        self.item_note.setText("포함/미포함은 이 릴리즈의 저장 상태입니다. 선택 모델 판정은 모델 범위를 함께 확인한 결과이며 실제 설비 적용 현황은 아닙니다.\n"
                               "같은 계열의 Item에 저장 행이 없으면 미확인입니다. 최신 버전이라는 이유로 이전 Item을 상속하지 않습니다.")
        self.item_empty.setVisible(not item_rows)
        self.item_empty.setText("확인된 포함 Item이 없습니다." if entry.items_verified else "Item 자료 미연결 또는 매핑 미확인 · Item이 없다고 확정하지 않습니다.")
        self.item_table.setVisible(bool(item_rows))
        sources = []
        self._append_sources(sources, "릴리즈", entry.sources)
        for item, *_ in item_rows:
            self._append_sources(sources, f"Item {item.item.item_id}", item.sources)
        self.source_model.set_rows(sources)
        self._set_mode("detail")
        for table in (self.info_table, self.item_table, self.source_table):
            _fit(table)
        self.back.setFocus()
        return True

    @staticmethod
    def _append_sources(rows, target, references):
        if not references:
            rows.append((target, "출처 미확인", "원본 위치 미확인", "미확인"))
        for ref in references:
            rows.append((f"{target} / {ref.field}", f"{ref.document or '문서 미확인'}\n리비전 {ref.revision or '미확인'}",
                         f"{ref.sheet or '시트 미확인'} · {ref.cells or '셀 미확인'}", str(ref.batch_id) if ref.batch_id is not None else "미확인"))

    def back_to_list(self):
        self._set_mode("list")
        if self.selected_id in self.list_model.ids:
            row = self.list_model.ids.index(self.selected_id)
            self.table.setCurrentIndex(self.list_model.index(row, 0))
            self.table.setFocus()

    def open_editor(self):
        """선택 릴리즈의 빈 편집 기반을 열고 실제 필드·DB 연결은 제공하지 않는다."""
        if self.mode != "detail" or self.catalog is None or self.selected_id is None:
            return
        if self.editor is not None and self.editor.isVisible():
            self.editor.raise_()
            return
        if self.editor is not None:
            self.editor.deleteLater()
        self.editor = VersionEditDialog(EditDraft(self.selected_id), title=self.detail_title.text(), parent=self)
        self.editor.open()
