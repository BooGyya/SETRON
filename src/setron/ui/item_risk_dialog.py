"""VER5 위험 Item 추가. 미리보기는 읽기 전용, 등록은 기존 관리 저장 경계에 위임한다."""

from copy import deepcopy

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QStyle, QStyleOptionButton, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import RiskStatus
from setron.ui.appearance import ui_color
from setron.ui.combo_box import ComboBox
from setron.ui.components import button, label
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.query_panel import _WrapRow
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import ACCENT_SOFT, INK, LINE, PRIMARY, SECONDARY, SOFT, font, set_widget_font
from setron.ui.version_data import family_display_name


VERSION_CRITERIA = (
    ("VERSION_BELOW", "{version} 미만 버전"),
    ("DIRECT_VERSION", "{version} 동일 버전"),
    ("VERSION_ABOVE", "{version} 초과 버전"),
)


def applied_rule(version, start, end, order=lambda _: None):
    """기존 사용 위험 입력을 저장 방식으로 대응한다. 구형 호출의 범위 검증도 유지한다."""
    if version is not None and (start is not None or end is not None):
        return None, {}, "위험 버전과 시작·해제 버전은 함께 지정할 수 없습니다. 한쪽만 고르세요."
    if version is not None:
        return "DIRECT_VERSION", {"release_version_id": version}, ""
    if start is None:
        return None, {}, ("해제 버전은 시작 버전과 함께 지정하세요." if end is not None
                          else "위험 버전 또는 시작 버전을 선택하세요.")
    first, last = order(start), order(end) if end is not None else None
    if end is not None and (end == start or first is not None and last is not None and first >= last):
        return None, {}, "해제 버전은 시작 버전보다 뒤의 버전이어야 합니다."
    return "DEFECT_ITEM", {"start_release_version_id": start, "end_release_version_id": end}, ""


def _paint_dropdown_arrow(widget, painter):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(ui_color(INK if widget.isEnabled() else SECONDARY), 1.6))
    center = QPointF(widget.width() - 18, widget.height() / 2)
    painter.drawPolyline(QPolygonF((center + QPointF(-4, -2), center + QPointF(0, 2), center + QPointF(4, -2))))
    painter.restore()


class _RiskComboBox(ComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        _paint_dropdown_arrow(self, painter)


def choice(name, *, custom_arrow=False):
    widget = _RiskComboBox() if custom_arrow else ComboBox()
    widget.setAccessibleName(name)
    widget.setProperty("queryField", True)
    widget.setMinimumWidth(0)
    widget.setMinimumContentsLength(8)
    widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    widget.currentTextChanged.connect(widget.setToolTip)
    widget.setFixedHeight(40)
    return widget


def field(name, control):
    holder = QWidget()
    body = QVBoxLayout(holder)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(6)
    body.addWidget(label(name, 12, medium=True, color=SECONDARY))
    body.addWidget(control)
    return holder


class _InputRow(_WrapRow):
    """조회 아이콘용 우측 정렬 없이 입력 필드가 같은 폭을 나눠 쓴다."""

    def _arrange(self, columns, *, force=False):
        super()._arrange(columns, force=force)
        self.grid.setAlignment(self.widgets[-1], Qt.AlignmentFlag(0))


class _ClampLabel(QLabel):
    """긴 문구를 지정한 줄 수 안에서 말줄임으로 줄이고 전체 문구는 툴팁으로 둔다."""

    def __init__(self, lines=2):
        super().__init__()
        self.lines, self.full = lines, ""
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)

    def set_full_text(self, text):
        self.full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        metrics, width = self.fontMetrics(), max(self.width(), 40)
        limit = metrics.lineSpacing() * self.lines + 1
        self.setMaximumHeight(metrics.lineSpacing() * self.lines + 4)

        def fits(text):
            return metrics.boundingRect(0, 0, width, 10000, int(Qt.TextFlag.TextWordWrap), text).height() <= limit
        text = self.full
        if not fits(text):
            low, high = 0, len(self.full)
            while low < high:
                mid = (low + high + 1) // 2
                low, high = (mid, high) if fits(self.full[:mid].rstrip() + "…") else (low, mid - 1)
            text = self.full[:low].rstrip() + "…"
        if text != self.text():
            self.setText(text)


class _ScopeButton(QPushButton):
    def __init__(self):
        super().__init__()
        self.setObjectName("riskModels")
        self.full_text = "대상 설비군 선택"
        self.setAccessibleName("대상 설비군 선택")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(40)

    def set_summary(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.setAccessibleDescription(text)
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, max(0, self.width() - 48)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "full_text"):
            self._elide()

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(40)
        return size

    def minimumSizeHint(self):
        return QSize(0, 40)

    def paintEvent(self, event):
        # 메뉴 버튼의 기본 글자 위치 대신 콤보 입력칸과 같은 기준선을 사용한다.
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        painter.setPen(ui_color(INK if self.isEnabled() else SECONDARY))
        painter.drawText(self.rect().adjusted(14, 0, -34, 0),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
        _paint_dropdown_arrow(self, painter)


def warning_icon(color="#856009", size=14):
    """글꼴에 없는 경고 기호 대신 삼각형 + 느낌표를 직접 그린 아이콘."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size * 2
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPolygon(QPolygonF([QPointF(s / 2, s * 0.08), QPointF(s * 0.96, s * 0.9), QPointF(s * 0.04, s * 0.9)]))
    painter.setPen(QColor("white"))
    painter.setBrush(QColor("white"))
    painter.drawRect(int(s * 0.46), int(s * 0.38), max(2, int(s * 0.08)), int(s * 0.27))
    painter.drawRect(int(s * 0.46), int(s * 0.72), max(2, int(s * 0.08)), max(2, int(s * 0.08)))
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class PopupDialog(QDialog):
    """앱 안의 모달 팝업: 제목줄 없이 메인 창 가운데에 뜨고, 자체 헤더(제목·닫기)와 그림자를 가진다. ESC로 닫힌다."""

    def __init__(self, parent, title, title_lines=1, *, show_close=True):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setWindowTitle(title)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 26)
        self.panel = QFrame()
        self.panel.setObjectName("popupPanel")
        self.panel.setStyleSheet("QFrame#popupPanel {background:white;border:1px solid #dee0e5;border-radius:14px;}")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(20, 32, 51, 70))
        self.panel.setGraphicsEffect(shadow)
        root.addWidget(self.panel)
        self.body = QVBoxLayout(self.panel)
        self.body.setContentsMargins(28, 20, 28, 22)
        self.body.setSpacing(14)
        header = QHBoxLayout()
        self.title = _ClampLabel(title_lines)
        set_widget_font(self.title, font(18, medium=True))
        self.title.set_full_text(title)
        header.addWidget(self.title, 1)
        self.close_x = None
        if show_close:
            self.close_x = QPushButton("×")
            self.close_x.setAccessibleName("닫기")
            self.close_x.setToolTip("닫기")
            self.close_x.setCursor(Qt.CursorShape.PointingHandCursor)
            self.close_x.setFixedSize(30, 30)
            self.close_x.setStyleSheet("QPushButton {border:0;border-radius:15px;background:transparent;color:#5b6880;padding:0;min-height:0;}"
                                       "QPushButton:hover {background:#f0f3f8;}")
            self.close_x.clicked.connect(self.reject)
            header.addWidget(self.close_x)
        self.body.addLayout(header)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.window().frameGeometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)


FIRST_HINT = "이 계열에서 이 Item이 처음 포함된 버전을 확정하지 못했습니다. 포함으로 확인된 릴리즈가 없거나, 버전 순서가 확인되지 않았거나, 같은 순서의 버전이 둘 이상이라 가려낼 수 없는 경우입니다."


class ItemRiskDialog(PopupDialog):
    def __init__(self, parent, data, gateway, context_provider, submit, *, family_id=None, item_id=None, edit_record=None):
        super().__init__(parent, "위험 기준 수정" if edit_record is not None else "위험 기준 등록",
                         show_close=edit_record is None)
        self.data, self.gateway, self.context_provider, self.submit = data, gateway, context_provider, submit
        self.edit_record = edit_record
        self.original_values = deepcopy(edit_record.current) if edit_record is not None else None
        self.legacy_rule = bool(self.original_values and self.original_values["rule_type"] not in dict(VERSION_CRITERIA))
        self.preview_values = None
        self.preview_valid = False
        self.suggested_name = ""
        self.submitted = False
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self._fit_height)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(350)
        self.preview_timer.timeout.connect(self.preview_impact)
        self.request = ReadRequest(self)
        self.request.completed.connect(self._previewed)
        self.setMinimumSize(420, 320)
        outer = self.body
        self.scroll = QScrollArea()
        self.scroll.setAccessibleName("위험 아이템 입력 영역")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("itemRiskForm")
        content.setStyleSheet("QWidget#itemRiskForm {background:white;}")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.name = QLineEdit()
        self.name.setMaxLength(250)
        self.name.setFixedHeight(40)
        self.name.setAccessibleName("위험 기준명")
        self.family, self.item = choice("관련 릴리즈 계열", custom_arrow=True), choice("관련 릴리즈노트 Item", custom_arrow=True)
        families = {p.family_id: p.family_name for p in data.versions.pairs}
        if edit_record is not None and family_id not in families:
            families[family_id] = f"계열 {family_id}"
        for fid, name in sorted(families.items()):
            self.family.addItem(family_display_name(name), fid)
        if family_id in families:
            self.family.setCurrentIndex(self.family.findData(family_id))
        self.item_search = QLineEdit()
        self.item_search.setFixedHeight(40)
        self.item_search.setPlaceholderText("관련 아이템 검색")
        self.item_search.setClearButtonEnabled(True)
        self.item_fixed, self.fixed_item_id = False, None
        self.item_editor = QWidget()
        editor = QVBoxLayout(self.item_editor)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.addWidget(self.item_search)
        editor.addWidget(_InputRow([self.family, self.item], 240, fields=True))
        layout.addWidget(self.item_editor)
        self.advanced = QWidget()
        advanced = QVBoxLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(14)
        advanced.addWidget(field("위험 기준명", self.name))
        self.criterion = choice("위험 판정 기준", custom_arrow=True)
        for key, caption in VERSION_CRITERIA:
            self.criterion.addItem(caption.format(version="기준 버전"), key)
        self.start = choice("기준 버전", custom_arrow=True)
        self.legacy_note = label("", 12, color=SECONDARY, wrap=True)
        self.legacy_note.hide()
        advanced.addWidget(self.legacy_note)
        self.version_fields = _InputRow([field("기준 버전", self.start), field("위험 판정 기준", self.criterion)], 220, fields=True)
        advanced.addWidget(self.version_fields)
        self.models = _ScopeButton()
        self.model_menu = QMenu(self.models)
        self.models.setMenu(self.model_menu)
        self.model_actions = []
        self.site, self.line = choice("위험 대상 Site", custom_arrow=True), choice("위험 대상 Line", custom_arrow=True)
        self.site.addItem("전체", None)
        for site in sorted({r.site for r in data.records}):
            self.site.addItem(site, site)
        self.scope_fields = QWidget()
        scope_fields = QHBoxLayout(self.scope_fields)
        scope_fields.setContentsMargins(0, 0, 0, 0)
        scope_fields.setSpacing(10)
        for title, control, stretch in (("Site", self.site, 3), ("Line", self.line, 3), ("설비군", self.models, 4)):
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            scope_fields.addWidget(field(title, control), stretch, Qt.AlignmentFlag.AlignBottom)
        advanced.addWidget(self.scope_fields)
        layout.addWidget(self.advanced)
        self.memo = QLineEdit()
        self.memo.setMaxLength(4000)
        self.memo.setPlaceholderText("예) 위험 기준 사유")
        self.memo.setFixedHeight(40)
        layout.addWidget(field("메모 (선택)", self.memo))
        self.target_summary = label("위험 적용 대상 0건", 18, medium=True, wrap=True)
        self.target_summary.setAccessibleName("현재 기준에 해당하는 설비 수")
        self.target_summary.setToolTip("현재 입력한 기준에 해당하는 전체 설비 수입니다. 기존 위험 후보를 포함하며, 미확인 설비는 포함하지 않습니다.")
        self.target_summary.hide()
        self.preview = label("영향 미리보기로 현재 관측에 미치는 영향을 확인하세요.", 12, color=SECONDARY, wrap=True)
        self.preview.setMinimumWidth(0)
        preview_box = QFrame()
        preview_box.setObjectName("impactPreview")
        preview_box.setStyleSheet("QFrame#impactPreview {background:#f4f5f8;border-radius:8px;}")
        preview_column = QVBoxLayout(preview_box)
        preview_column.setContentsMargins(12, 10, 12, 10)
        preview_column.setSpacing(8)
        preview_column.addWidget(self.target_summary)
        preview_column.addWidget(self.preview)
        layout.addWidget(preview_box)
        self.impact_model = ReadTableModel(("설비", "위치", "SW 버전"), centered=True)
        self.impact_table = read_table(self.impact_model, "영향 미리보기 설비", (130, 160, 210))
        self.impact_table.fit_to_viewport = True
        self.impact_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.impact_table.setStyleSheet(self.impact_table.styleSheet().replace("text-align:left", "text-align:center"))
        layout.addWidget(self.impact_table)
        layout.addStretch()
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        # 취소·등록은 스크롤 밖에 두어 낮은 화면에서도 항상 접근할 수 있다.
        self.footer = QWidget()
        footer = QVBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        footer.addLayout(row)
        row.setSpacing(12)
        self.preview_button, self.cancel_button, self.save_button = button("다시 계산"), button("취소"), button("등록", primary=True)
        self.save_button.setEnabled(False)
        self.preview_button.clicked.connect(self.preview_impact)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.save)
        self.next_step = label("", 12, wrap=True)
        self.next_step.setAccessibleName("저장 안내")
        self.next_step.setMinimumWidth(0)
        row.addWidget(self.next_step, 1)
        row.addWidget(self.preview_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.save_button)
        outer.addWidget(self.footer)
        self._configure_dropdowns()
        self.family.currentIndexChanged.connect(self._family_changed)
        self.item.currentIndexChanged.connect(self._item_changed)
        self.item_search.textChanged.connect(self._filter_items)
        self.site.currentIndexChanged.connect(self._site_changed)
        self.criterion.currentIndexChanged.connect(self._criterion_changed)
        for control in (self.start, self.line):
            control.currentIndexChanged.connect(self._invalidate)
        self.start.currentIndexChanged.connect(self._start_changed)
        # 메모는 영향 계산과 무관하므로 입력해도 미리보기를 다시 계산하지 않는다.
        self.name.textChanged.connect(self._invalidate)
        self._family_changed()
        if item_id is not None and self.item.findData(item_id) >= 0:
            self.item.setCurrentIndex(self.item.findData(item_id))
            self.item_fixed, self.fixed_item_id = True, item_id
        self._update_editor()
        self._site_changed()
        self._criterion_changed()
        if edit_record is not None:
            self._restore_edit_record()
        width = 720
        if screen := self.screen():
            available = screen.availableGeometry()
            width = min(width, available.width() - 32)
        self.resize(width, self.minimumHeight())
        self._fit_height()

    def _restore_edit_record(self):
        """현재 DB 행을 채우고 기존 계열·Item 연결과 구형 판정 조건은 유지한다."""
        row = self.original_values
        presentation = (row.get("target_reference") or {}).get("item_manager") or {}
        self.family.setEnabled(False)
        item_id = row.get("item_id")
        if item_id is not None and self.item.findData(item_id) < 0:
            entry = next((e for e in self.data.versions.items if e.item.item_id == item_id), None)
            self.item.addItem(entry.item.content if entry else f"Item {item_id}", item_id)
        self.item_fixed, self.fixed_item_id = True, item_id
        self.item.setCurrentIndex(self.item.findData(item_id))
        self._update_editor()
        key = row["rule_type"]
        if self.legacy_rule:
            caption = {"SAFETY_REQUIRED": "환경안전 필수", "DEFECT_ITEM": "결함 Item 범위"}.get(key, key)
            self.criterion.addItem(caption, key)
            self.version_fields.hide()
            versions = {e.release.release_version_id: e.release.version_raw for e in self.data.versions.releases}
            start = versions.get(row.get("start_release_version_id"), "미지정")
            end = versions.get(row.get("end_release_version_id"), "미지정")
            self.legacy_note.setText(f"기존 {caption} 조건을 유지합니다. 시작 버전 {start} · 해제 버전 {end}")
            self.legacy_note.show()
        self.criterion.setCurrentIndex(self.criterion.findData(key))
        self.start.setCurrentIndex(self.start.findData(row.get("release_version_id")))
        self.criterion.setEnabled(not self.legacy_rule)
        self.start.setEnabled(not self.legacy_rule)
        site, line = presentation.get("site"), presentation.get("line")
        if site is not None and self.site.findData(site) < 0:
            self.site.addItem(site, site)
        self.site.setCurrentIndex(max(0, self.site.findData(site)))
        self._site_changed()
        if line is not None and self.line.findData(line) < 0:
            self.line.addItem(line, line)
        self.line.setCurrentIndex(max(0, self.line.findData(line)))
        selected = presentation.get("model_ids")
        if selected is None and row.get("group_id") is not None:
            selected = [row["group_id"]]
        for mid in selected or ():
            if not any(a.data() == mid for a in self.model_actions):
                action = self.model_menu.addAction(f"모델 {mid}")
                action.setCheckable(True)
                action.setData(mid)
                action.toggled.connect(self._models_changed)
                self.model_actions.append(action)
        for action in self.model_actions:
            action.setChecked(selected is None or action.data() in selected)
        self._models_changed()
        self._original_scope = (tuple(a.data() for a in self.model_actions if a.isChecked()), site, line)
        self.name.setText(presentation.get("name") or row["risk_reason"])
        self.memo.setText(presentation.get("memo") or "")
        self.suggested_name = ""
        self.save_button.setText("수정 저장")
        self._invalidate()

    def _queue_fit(self):
        if self.isVisible():
            self.fit_timer.start(0)

    def _fit_height(self):
        """입력·미리보기 높이만큼 표시하고 화면을 넘는 부분만 내부에서 스크롤한다."""
        self.layout().activate()
        self.body.activate()
        content = self.scroll.widget()
        content.layout().activate()
        width = max(1, self.scroll.viewport().width())
        natural = content.layout().heightForWidth(width)
        if natural < 0:
            natural = content.sizeHint().height()
        natural = max(natural, content.minimumSizeHint().height())
        overhead = self.height() - self.scroll.viewport().height()
        bounds = self.screen().availableGeometry().adjusted(16, 16, -16, -16)
        maximum = min(bounds.height(), self.maximumHeight())
        wanted = max(self.minimumHeight(), min(natural + overhead, maximum))
        if wanted != self.height():
            center = self.frameGeometry().center()
            self.resize(self.width(), wanted)
            if self.isVisible():
                y = max(bounds.top(), min(center.y() - self.height() // 2, bounds.bottom() - self.height() + 1))
                self.move(self.x(), y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width() and hasattr(self, "footer"):
            self._queue_fit()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "footer"):
            self._queue_fit()

    def _set_preview(self, text):
        self.preview.setText(text)
        self.preview.setVisible(bool(text))

    def _configure_dropdowns(self):
        """공통 콤보의 위치·키보드 동작을 유지하고 이 팝업의 필드와 목록 모양만 맞춘다."""
        input_style = (
            f"QLineEdit {{background:{SOFT};color:{INK};border:1px solid {LINE};border-radius:8px;padding:0 12px;}}"
            f"QLineEdit:hover {{border-color:{SECONDARY};}}"
            f"QLineEdit:focus {{background:white;border:2px solid {PRIMARY};}}")
        for control in (self.name, self.memo, self.item_search):
            control.setStyleSheet(input_style)
            set_widget_font(control, font(14))
        field_style = (
            f"QComboBox {{background:{SOFT};color:{INK};border:1px solid {LINE};border-radius:8px;padding:0 34px 0 12px;}}"
            f"QComboBox:hover {{background:white;border-color:{SECONDARY};}}"
            f"QComboBox:focus, QComboBox:on {{background:{ACCENT_SOFT};border:2px solid {PRIMARY};}}"
            f"QComboBox:disabled {{background:{SOFT};color:{SECONDARY};border-color:{LINE};}}"
            "QComboBox::drop-down {subcontrol-origin:padding;subcontrol-position:top right;width:28px;border:0;}"
            "QComboBox::down-arrow {image:none;width:0;height:0;}")
        list_style = (
            f"QAbstractItemView {{background:white;color:{INK};border:1px solid {LINE};border-radius:8px;"
            f"padding:4px;outline:0;selection-background-color:{ACCENT_SOFT};selection-color:{PRIMARY};}}"
            "QAbstractItemView::item {min-height:22px;padding:6px 10px 6px 5px;border:0;border-radius:5px;}"
            f"QAbstractItemView::item:hover {{background:{SOFT};}}"
            f"QAbstractItemView::item:selected {{background:{ACCENT_SOFT};color:{PRIMARY};}}")
        for control in (self.family, self.item, self.start, self.criterion, self.site, self.line):
            control.setStyleSheet(field_style)
            control.setMaxVisibleItems(8)
            set_widget_font(control, font(14))
            view = control.view()
            view.setItemDelegate(QStyledItemDelegate(view))
            view.setUniformItemSizes(True)
            view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            view.setMouseTracking(True)
            view.setStyleSheet(list_style)
            set_widget_font(view, font(14))
        self.models.setStyleSheet(
            f"QPushButton#riskModels {{background:{SOFT};color:{INK};border:1px solid {LINE};border-radius:8px;"
            "padding:0 34px 0 12px;text-align:left;min-height:0;}"
            f"QPushButton#riskModels:hover {{background:white;border-color:{SECONDARY};}}"
            f"QPushButton#riskModels:focus, QPushButton#riskModels:pressed {{background:{ACCENT_SOFT};border:2px solid {PRIMARY};}}"
            "QPushButton#riskModels::menu-indicator {image:none;width:0;height:0;}")
        set_widget_font(self.models, font(14))
        self.model_menu.setStyleSheet(
            f"QMenu {{background:white;color:{INK};border:1px solid {LINE};border-radius:8px;padding:4px 0;}}"
            "QMenu::item {min-height:22px;padding:6px 12px 6px 0;border-radius:5px;}"
            f"QMenu::item:selected {{background:{ACCENT_SOFT};color:{PRIMARY};}}"
            "QMenu::indicator {width:14px;height:14px;}")
        set_widget_font(self.model_menu, font(14))
        self.model_menu.aboutToShow.connect(lambda: self.model_menu.setMinimumWidth(self.models.width()))

    def _family_changed(self, *_):
        fid = self.family.currentData()
        releases = [e.release for e in self.data.versions.releases if e.release.family_id == fid]
        ids = {r.release_version_id for r in releases}
        item_ids = {r.item_id for r in self.data.versions.version_items if r.release_version_id in ids}
        self.family_item_ids = item_ids
        self._filter_items()
        self.start.clear()
        for release in sorted(releases, key=lambda r: (r.version_order is None, r.version_order or 0, r.release_version_id)):
            # 버전 이름만 보인다. 순서 값은 사용자가 이미 아는 정보라 뺐다 (11-11 점검).
            # 순서가 미확인인 버전은 미만·초과 기준으로 등록할 수 없음을 함께 표시한다.
            text = release.version_raw if release.version_order is not None else f"{release.version_raw} · 순서 미확인"
            self.start.addItem(text, release.release_version_id)
        self.model_menu.clear()
        self.model_actions = []
        models = {p.model_id: p.model_name for p in self.data.versions.pairs if p.family_id == fid}
        for mid, name in models.items():
            action = self.model_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(True)
            action.setData(mid)
            action.toggled.connect(self._models_changed)
            self.model_actions.append(action)
        self._models_changed()
        self._invalidate()

    def _filter_items(self, *_):
        previous = self.item.currentData()
        self.item.clear()
        for entry in self.data.search(self.item_search.text()):
            if entry.item.item_id in self.family_item_ids:
                self.item.addItem(f"No {entry.item.item_no or '미확인'} · {entry.item.content[:65]}", entry.item.item_id)
        if self.item.findData(previous) >= 0:
            self.item.setCurrentIndex(self.item.findData(previous))

    def _suggestion(self):
        """Item 장문을 반복하지 않고 계열·기준 버전·비교 조건으로 기준명을 제안한다."""
        version = self._reference_version()
        if version is None:
            return ""
        relation = {"VERSION_BELOW": "미만 사용 위험", "DIRECT_VERSION": "사용 위험", "VERSION_ABOVE": "초과 사용 위험"}
        suffix = relation.get(self.criterion.currentData())
        return f"{self.family.currentText()} {version} {suffix}".strip() if suffix else ""

    def _reference_version(self):
        release = next((entry.release for entry in self.data.versions.releases
                        if entry.release.release_version_id == self.start.currentData()), None)
        return release.version_raw if release else None

    def _suggest_name(self):
        """사용자가 기준명을 직접 고치지 않았으면 제안으로 채운다. 판정 기준을 바꿔도 기준명을 비우지 않는다."""
        suggestion = self._suggestion()
        if not self.name.text().strip() or self.name.text() == self.suggested_name:
            self.name.setText(suggestion)
        self.suggested_name = suggestion

    def _start_changed(self, *_):
        version = self._reference_version() or "기준 버전"
        with QSignalBlocker(self.criterion):
            for index, (_key, caption) in enumerate(VERSION_CRITERIA):
                self.criterion.setItemText(index, caption.format(version=version))
        self.criterion.setToolTip(self.criterion.currentText())
        self._suggest_name()

    def _item_changed(self, *_):
        self._suggest_name()
        self._invalidate()

    def _models_changed(self, *_):
        self.models.set_summary(" · ".join(a.text() for a in self.model_actions if a.isChecked()) or "대상 설비군 선택")
        self._invalidate()

    def _site_changed(self, *_):
        site = self.site.currentData()
        self.line.clear()
        self.line.addItem("전체", None)
        self.line.setEnabled(site is not None)
        for line in sorted({r.line for r in self.data.records if r.site == site and r.line}):
            self.line.addItem(line, line)
        self._invalidate()

    def _update_editor(self, *_):
        self.item_editor.setVisible(not self.item_fixed)
        self.family.setEnabled(not self.item_fixed)
        self.item.setEnabled(not self.item_fixed)

    def _criterion_changed(self, *_):
        self._suggest_name()
        self._invalidate()

    def values(self):
        key = self.criterion.currentData()
        original = self.original_values or {}
        values = {field: original[field] for field in (
            "group_id", "item_id", "start_release_version_id", "end_release_version_id", "is_enabled", "mapping_status",
        ) if field in original}
        presentation = (original.get("target_reference") or {}).get("item_manager") or {}
        models = [a.data() for a in self.model_actions if a.isChecked()]
        values.update(rule_type=key, family_id=original.get("family_id", self.family.currentData()),
                      risk_reason=self.name.text().strip(),
                      release_version_id=original.get("release_version_id") if self.legacy_rule else self.start.currentData(),
                      mapping_status=original.get("mapping_status", "VERIFIED"),
                      item_manager=dict(schema=1, name=self.name.text().strip(),
                                        category=presentation.get("category", "결함 버전"),
                                        memo=self.memo.text().strip(), model_ids=models,
                                        site=self.site.currentData(), line=self.line.currentData()))
        if original.get("group_id") is not None and tuple(models) != self._original_scope[0]:
            # 명시적으로 모델 범위를 바꾼 경우 새 목록을 적용한다. 이전 단일 모델과의
            # 숨은 교집합 때문에 화면에서 고른 모델이 판정에서 빠지지 않게 한다.
            values["group_id"] = None
        item_id = self.fixed_item_id if self.item_fixed else self.item.currentData()
        if item_id is not None:
            values["item_id"] = item_id
        # 원래 위치·모델 제한이 없던 구형 기준은 이름만 고칠 때도 새 제한을 만들지 않는다.
        if self.original_values is not None and not presentation and not self.memo.text().strip() and \
                (tuple(models), self.site.currentData(), self.line.currentData()) == self._original_scope:
            values.pop("item_manager")
        return values

    @staticmethod
    def _same_impact(first, second):
        """메모만 다른 입력은 같은 영향 계산으로 본다. 영향에 쓰이는 값은 모두 비교한다."""
        if first is None or second is None:
            return False
        def core(values):
            values = deepcopy(values)
            if "item_manager" in values:
                values["item_manager"]["memo"] = ""
            return values
        return core(first) == core(second)

    def _invalidate(self, *_):
        self.request.cancel()
        self.preview_valid = False
        self.impact_table.hide()
        self.preview_values = None
        self.save_button.setEnabled(False)
        self.preview_button.hide()
        self.target_summary.hide()
        self._set_preview("현재 입력값으로 영향을 확인하고 있습니다.")
        self.next_step.setText("영향을 계산하고 있습니다. 잠시 후 저장할 수 있습니다.")
        if self.isVisible():
            self.preview_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._queue_fit()
        self.preview_timer.start(350)

    def hideEvent(self, event):
        self.fit_timer.stop()
        self.preview_timer.stop()
        self.request.cancel()
        super().hideEvent(event)

    def _update_save_state(self, *_):
        ready = self.preview_valid and not self.submitted
        self.save_button.setEnabled(ready)
        if self.preview_valid:
            self.next_step.setText("")
            action = "수정하면" if self.edit_record is not None else "등록하면"
            self.save_button.setToolTip(action + " 선택한 Item·버전·설비군이 위험 기준으로 저장되고, 설비별 판정과 대시보드·히트맵 위험 집계에 반영됩니다.")


    def preview_impact(self):
        self.preview_timer.stop()
        if self.request.busy or self.submitted:
            return
        values = deepcopy(self.values())
        self.target_summary.hide()
        presentation = values.get("item_manager", {"name": values["risk_reason"],
                                                   "model_ids": [a.data() for a in self.model_actions if a.isChecked()]})
        problem = ""
        if not presentation["name"]:
            problem = "위험 기준명을 입력하세요."
        elif not presentation["model_ids"]:
            problem = "대상 설비군을 선택하세요."
        elif not self.legacy_rule and not values.get("release_version_id"):
            problem = "기준 버전을 선택하세요."
        if problem:
            self._set_preview(problem)
            self.next_step.setText(problem)
            self._queue_fit()
            return
        context, gateway = self.context_provider(), self.gateway
        self.preview_valid = False
        self.save_button.setEnabled(False)
        self.preview_values = values
        self._set_preview("영향 계산 중…")
        self.preview_button.setEnabled(False)
        self.request.start(BackgroundRead(lambda: gateway.preview_risk_rule(values, context)))

    def _previewed(self, result, error):
        self.preview_button.setEnabled(True)
        if error is not None or result is None:
            self.target_summary.hide()
            self.preview_values = None
            self._set_preview("미리보기를 불러오지 못했습니다.")
            self.preview_button.show()
            self.next_step.setText("연결을 확인한 뒤 다시 계산하세요.")
            self._queue_fit()
            return
        if not self._same_impact(self.preview_values, self.values()):
            return
        has_base = bool(result.base_date) and not result.problems
        self.target_summary.setVisible(has_base)
        if has_base:
            self.target_summary.setText(f"위험 적용 대상 {result.count(RiskStatus.RISK)}건")
        if result.problems:
            self._set_preview(" · ".join(result.problems))
        elif not has_base:
            self._set_preview("설비 관측 자료가 없어 영향 설비를 계산할 수 없습니다.")
        else:
            unknown = result.count(RiskStatus.UNKNOWN)
            self._set_preview(f"미확인 {unknown}건 · 판정 자료 확인 필요" if unknown else "")
        rows = []
        for impact in getattr(result, "equipment", ()):
            versions = dict.fromkeys(
                finding.pm.version_raw if finding.pm.version_raw and finding.pm.version_raw.strip() else "버전 미입력"
                for finding in impact.findings if finding.pm is not None)
            record = impact.record
            location = " · ".join(filter(None, (record.site, record.line)))
            rows.append((record.equipment_code, location, " / ".join(versions) or "버전 미입력"))
        self.impact_model.set_rows(rows)
        self.impact_table.resizeRowsToContents()
        self.impact_table.setFixedHeight(min(280, max(110, 50 + sum(self.impact_table.rowHeight(i) for i in range(min(6, len(rows)))))))
        self.impact_table.setVisible(bool(rows))
        self.preview_valid = result.saveable
        if result.problems:
            self.next_step.setText(result.problems[0])
        self._update_save_state()
        self._queue_fit()

    def save(self):
        if self.submitted or not self.save_button.isEnabled() or not self._same_impact(self.preview_values, self.values()):
            return False
        self.submitted = True
        if self.submit(deepcopy(self.values())):
            self.accept()
            return True
        self.submitted = False
        self.next_step.setText("진행 중인 관리 작업을 마친 뒤 저장하세요.")
        return False

    def reject(self):
        self.fit_timer.stop()
        self.preview_timer.stop()
        self.request.cancel()
        super().reject()
