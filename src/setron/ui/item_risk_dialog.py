"""VER5 위험 Item 추가. 미리보기는 읽기 전용, 등록은 기존 관리 저장 경계에 위임한다."""

from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import CRITERION_LABELS, FindingBasis, RiskKind, RiskStatus
from setron.ui.combo_box import ComboBox
from setron.ui.components import StatusBadge, button, label
from setron.ui.item_original_table import show_item_source
from setron.ui.equipment import ReadTableModel, read_table
from setron.ui.query_panel import _WrapRow
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import PRIMARY, SECONDARY, font, set_widget_font
from setron.ui.version_data import family_display_name


# 구분은 판정 기준에서 정한다. 판정 기준과 따로 고르면 같은 내용을 두 번 고르게 되고, 구분만 바꿔도
# 판정이 바뀌지 않아 헷갈렸다 (11-11 점검). 목록의 구분 칩은 이 값을 그대로 보여 준다.
CATEGORY_BY_RULE = {"SAFETY_REQUIRED": "환경안전 필수", "DEFECT_ITEM": "결함 버전", "DIRECT_VERSION": "결함 버전"}
APPLIED, NOT_APPLIED = RiskKind.APPLIED.value, RiskKind.NOT_APPLIED.value


def applied_rule(version, start, end, order=lambda _: None):
    """'Item 사용 시 위험' 입력을 기존 저장 방식으로 바꾼다. (rule_type, 저장 필드, 문제) 를 돌려준다.

    - 위험 버전만 지정: DIRECT_VERSION (그 버전을 쓰면 위험).
    - 시작 버전(+ 선택 해제 버전) 지정: DEFECT_ITEM (선택한 Item이 [시작, 해제) 범위의 버전에 들어 있으면 위험).
    - 그 밖의 조합은 어느 방식인지 모호하므로 저장하지 않고 이유를 돌려준다. ``order``는 버전 ID → 순서 값이다.
    """
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


def choice(name):
    widget = ComboBox()
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

    def __init__(self, parent, title, title_lines=1):
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
        self.close_x = QPushButton("×")
        self.close_x.setAccessibleName("닫기")
        self.close_x.setToolTip("닫기")
        self.close_x.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_x.setFixedSize(30, 30)
        self.close_x.setStyleSheet("QPushButton {border:0;border-radius:15px;background:transparent;color:#5b6880;padding:0;min-height:0;}"
                                   "QPushButton:hover {background:#f0f3f8;}")
        self.close_x.clicked.connect(self.reject)
        header.addWidget(self.title, 1)
        header.addWidget(self.close_x)
        self.body.addLayout(header)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            center = parent.window().frameGeometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)


def _link_style(widget):
    """보조 동작은 테두리 없는 텍스트 링크 모양으로 낮춘다."""
    set_widget_font(widget, font(12, medium=True))
    widget.setFixedHeight(24)
    widget.setStyleSheet(f"QPushButton {{border:0;background:transparent;color:{PRIMARY};padding:0 4px;min-height:0;}}"
                         "QPushButton:hover {background:#edf0fd;border-radius:6px;}")


FIRST_HINT = "이 계열에서 이 Item이 처음 포함된 버전을 확정하지 못했습니다. 포함으로 확인된 릴리즈가 없거나, 버전 순서가 확인되지 않았거나, 같은 순서의 버전이 둘 이상이라 가려낼 수 없는 경우입니다."


def _stat(caption):
    """숫자를 크게, 이름을 작게 보여 주는 영향 지표 한 칸."""
    frame = QFrame()
    frame.setObjectName("impactStat")
    frame.setStyleSheet("QFrame#impactStat {background:white;border:1px solid #e5eaf3;border-radius:8px;}")
    column = QVBoxLayout(frame)
    column.setContentsMargins(12, 8, 12, 8)
    column.setSpacing(0)
    value = label("0", 18, medium=True)
    column.addWidget(value)
    column.addWidget(label(caption, 11, color=SECONDARY))
    return frame, value


class ItemRiskDialog(PopupDialog):
    def __init__(self, parent, data, gateway, context_provider, submit, *, family_id=None, item_id=None):
        super().__init__(parent, "위험 기준 등록")
        self.data, self.gateway, self.context_provider, self.submit = data, gateway, context_provider, submit
        self.preview_values = None
        self.preview_valid = False
        self.suggested_name = ""
        self.submitted = False
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(350)
        self.preview_timer.timeout.connect(self.preview_impact)
        self.request = ReadRequest(self)
        self.request.completed.connect(self._previewed)
        self.setMinimumSize(420, 320)
        self.setStyleSheet("QLineEdit,QComboBox {border:1px solid #dee0e5;border-radius:8px;padding:6px 10px;}")
        outer = self.body
        self.scroll = QScrollArea()
        self.scroll.setAccessibleName("위험 아이템 입력 영역")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("itemRiskForm")
        content.setStyleSheet("QWidget#itemRiskForm {background:white;}")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        # 핵심 입력(Item 확인 · 메모 · 영향 요약)만 먼저 보이고, 나머지는 기본값으로 채운 채 '상세 설정'에 둔다.
        self.name = QLineEdit()
        self.name.setMaxLength(250)
        self.name.setFixedHeight(40)
        self.name.setAccessibleName("위험 기준명")
        self.family, self.item = choice("관련 릴리즈 계열"), choice("관련 릴리즈노트 Item")
        families = {p.family_id: p.family_name for p in data.versions.pairs}
        for fid, name in sorted(families.items()):
            self.family.addItem(family_display_name(name), fid)
        if family_id in families:
            self.family.setCurrentIndex(self.family.findData(family_id))
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("관련 아이템 검색")
        self.item_search.setClearButtonEnabled(True)
        # Item 내용은 한 덩어리 제목(2줄 말줄임)으로, 계열·최초 포함 버전·원본 행 유무는 그 아래 작은 칩으로 나눈다.
        self.item_note = _ClampLabel(2)
        set_widget_font(self.item_note, font(14))
        self.item_family_chip, self.item_first_chip, self.item_source_chip = StatusBadge(""), StatusBadge(""), StatusBadge("")
        self.item_meta = QWidget()
        meta = QHBoxLayout(self.item_meta)
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        for chip in (self.item_family_chip, self.item_first_chip, self.item_source_chip):
            meta.addWidget(chip)
        meta.addStretch()
        self.source_button = button("원본 행 보기")
        self.source_button.clicked.connect(lambda: show_item_source(self, data, self.item.currentData()))
        self.change_item = button("아이템 변경")
        self.item_fixed = item_id is not None
        for small in (self.source_button, self.change_item):
            _link_style(small)
        self.change_item.setCheckable(True)
        self.change_item.setChecked(item_id is None)
        self.item_editor = QWidget()
        editor = QVBoxLayout(self.item_editor)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.addWidget(self.item_search)
        editor.addWidget(_WrapRow([self.family, self.item], 240, fields=True))
        item_box = QWidget()
        item_column = QVBoxLayout(item_box)
        item_column.setContentsMargins(0, 0, 0, 0)
        item_column.setSpacing(8)
        item_column.addWidget(self.item_note)
        item_column.addWidget(self.item_meta)
        item_actions = QHBoxLayout()
        item_actions.setSpacing(6)
        item_actions.addWidget(self.change_item)
        item_actions.addWidget(self.source_button)
        item_actions.addStretch()
        item_column.addLayout(item_actions)
        layout.addWidget(item_box)
        layout.addWidget(self.item_editor)
        self.change_item.toggled.connect(self._update_editor)
        self.memo = QLineEdit()
        self.memo.setMaxLength(4000)
        self.memo.setPlaceholderText("예) 원본 확인일 · 대상 공정")
        self.memo.setFixedHeight(40)
        layout.addWidget(field("메모 (선택)", self.memo))
        # 범위(계열·Site·Line)는 칩, 결과는 숫자 지표, 자료가 없으면 한 문장 안내로 나눠 보인다.
        self.preview = label("영향 미리보기로 현재 관측에 미치는 영향을 확인하세요.", 12, color=PRIMARY, wrap=True)
        self.preview.setMinimumWidth(0)
        self.scope_chips = [StatusBadge("") for _ in range(3)]
        scope_row = QHBoxLayout()
        scope_row.setSpacing(6)
        for chip in self.scope_chips:
            scope_row.addWidget(chip)
        scope_row.addStretch()
        self.stats = QWidget()
        stats_row = QHBoxLayout(self.stats)
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(8)
        self.stat_values = {}
        for key, caption in (("risk", "위험 후보"), ("unknown", "미확인"), ("new", "새 위험 후보"), ("hpa", "HPA 제외")):
            frame, value = _stat(caption)
            self.stat_values[key] = value
            stats_row.addWidget(frame, 1)
        self.stats.hide()
        preview_box = QFrame()
        preview_box.setObjectName("impactPreview")
        preview_box.setStyleSheet("QFrame#impactPreview {background:#f4f5f8;border-radius:8px;}")
        preview_column = QVBoxLayout(preview_box)
        preview_column.setContentsMargins(12, 10, 12, 10)
        preview_column.setSpacing(8)
        preview_column.addLayout(scope_row)
        preview_column.addWidget(self.stats)
        preview_column.addWidget(self.preview)
        layout.addWidget(preview_box)
        self.advanced_toggle = button("상세 설정 펼치기")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        layout.addWidget(self.advanced_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.advanced = QWidget()
        advanced = QVBoxLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(14)
        advanced.addWidget(field("위험 기준명", self.name))
        self.criterion = choice("위험 판정 기준")
        # 판정 기준은 두 가지다 (미적용 위험 / 적용 위험). '사용 시 위험'은 입력한 버전에 따라 기존 방식으로 저장한다.
        self.criterion.addItem(CRITERION_LABELS[RiskKind.NOT_APPLIED], NOT_APPLIED)
        self.criterion.addItem(CRITERION_LABELS[RiskKind.APPLIED], APPLIED)
        self.criterion.setItemData(0, "필수 Item이 포함된 버전 이상(누적 적용)을 쓰지 않으면 위험", Qt.ItemDataRole.ToolTipRole)
        self.criterion.setItemData(1, "위험 버전이나 결함 Item이 든 시작~해제 범위의 버전을 쓰면 위험", Qt.ItemDataRole.ToolTipRole)
        self.models = QPushButton("대상 설비군 선택")
        self.models.setFixedHeight(40)
        self.model_menu = QMenu(self.models)
        self.models.setMenu(self.model_menu)
        self.model_actions = []
        advanced.addWidget(_WrapRow([field("위험 판정 기준", self.criterion), field("대상 설비군 · 복수 선택", self.models)], 240, fields=True))
        self.criterion_hint = label("", 12, color=SECONDARY, wrap=True)
        self.criterion_hint.setAccessibleName("판정 방식 안내")
        self.criterion_hint.setMinimumWidth(0)
        advanced.addWidget(self.criterion_hint)
        # 위험 버전 하나만 고르면 그 버전 사용이 위험, 시작(~해제) 버전을 고르면 그 범위에 든 Item 사용이 위험이다.
        self.version, self.start, self.end = choice("위험 버전"), choice("시작 버전"), choice("해제 버전")
        self.version_fields = QWidget()
        versions = QVBoxLayout(self.version_fields)
        versions.setContentsMargins(0, 0, 0, 0)
        versions.setSpacing(14)
        versions.addWidget(field("위험 버전 (선택 · 이 버전을 쓰면 위험)", self.version))
        versions.addWidget(_WrapRow([field("시작 버전 (선택 · 포함)", self.start), field("해제 버전 (선택 · 제외)", self.end)],
                                    240, fields=True))
        advanced.addWidget(self.version_fields)
        self.site, self.line = choice("위험 대상 Site"), choice("위험 대상 Line")
        self.site.addItem("전체 Site", None)
        for site in sorted({r.site for r in data.records}):
            self.site.addItem(site, site)
        advanced.addWidget(field("대상 범위", _WrapRow([self.site, self.line], 240, fields=True)))
        self.impact_model = ReadTableModel(("설비 · 위치", "판정 결과", "해당 PM · 버전 및 근거"))
        self.impact_table = read_table(self.impact_model, "영향 미리보기 설비", (105, 108, 198))
        advanced.addWidget(self.impact_table)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
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
        self.family.currentIndexChanged.connect(self._family_changed)
        self.item.currentIndexChanged.connect(self._item_changed)
        self.item_search.textChanged.connect(self._filter_items)
        self.site.currentIndexChanged.connect(self._site_changed)
        self.criterion.currentIndexChanged.connect(self._criterion_changed)
        for control in (self.version, self.start, self.end):
            control.currentIndexChanged.connect(self._versions_changed)
        self.line.currentIndexChanged.connect(self._invalidate)
        # 메모는 영향 계산과 무관하므로 입력해도 미리보기를 다시 계산하지 않는다.
        self.name.textChanged.connect(self._invalidate)
        self._family_changed()
        if item_id is not None and self.item.findData(item_id) >= 0:
            self.item.setCurrentIndex(self.item.findData(item_id))
            entry = next(e for e in data.versions.items if e.item.item_id == item_id)
            self.name.setText(entry.item.content[:250])
        self.change_item.setChecked(item_id is None or self.item.currentData() != item_id)
        self._site_changed()
        self._criterion_changed()
        width, height = 600, 560
        if screen := self.screen():
            available = screen.availableGeometry()
            width, height = min(width, available.width() - 32), min(height, available.height() - 32)
        self.resize(width, height)

    def _family_changed(self, *_):
        fid = self.family.currentData()
        releases = [e.release for e in self.data.versions.releases if e.release.family_id == fid]
        ids = {r.release_version_id for r in releases}
        item_ids = {r.item_id for r in self.data.versions.version_items if r.release_version_id in ids}
        self.family_item_ids = item_ids
        self._filter_items()
        self.version_orders = {r.release_version_id: r.version_order for r in releases}
        for combo, empty in ((self.version, "지정 안 함"), (self.start, "지정 안 함"), (self.end, "해제 버전 없음")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(empty, None)
        for release in sorted(releases, key=lambda r: (r.version_order is None, r.version_order or 0, r.release_version_id)):
            # 버전 이름만 보인다. 순서 값은 사용자가 이미 아는 정보라 뺐다 (11-11 점검).
            # 순서를 모르는 버전만 표시를 남긴다: 결함 범위 판정이 '미확인'이 될 수 있다.
            text = release.version_raw if release.version_order is not None else f"{release.version_raw} · 순서 미확인"
            for combo in (self.version, self.start, self.end):
                combo.addItem(text, release.release_version_id)
        for combo in (self.version, self.start, self.end):
            combo.blockSignals(False)
            combo.setToolTip(combo.currentText())
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
        self._versions_changed()

    def _filter_items(self, *_):
        previous = self.item.currentData()
        self.item.clear()
        for entry in self.data.search(self.item_search.text()):
            if entry.item.item_id in self.family_item_ids:
                self.item.addItem(f"No {entry.item.item_no or '미확인'} · {entry.item.content[:65]}", entry.item.item_id)
        if self.item.findData(previous) >= 0:
            self.item.setCurrentIndex(self.item.findData(previous))

    def _rule(self):
        """현재 입력의 저장 방식. (rule_type, 저장 필드, 문제). 미적용 위험은 늘 SAFETY_REQUIRED다."""
        if self.criterion.currentData() == NOT_APPLIED:
            return "SAFETY_REQUIRED", {}, ""
        return applied_rule(self.version.currentData(), self.start.currentData(), self.end.currentData(),
                            lambda vid: self.version_orders.get(vid))

    def _suggestion(self):
        """판정 기준에 맞는 기준명 제안. 위험 버전만 지정하면 계열·버전으로, 나머지는 고른 Item 내용으로 짓는다."""
        if self._rule()[0] == "DIRECT_VERSION":
            version = self.version.currentText().split(" · ")[0].strip()
            return f"{self.family.currentText()} {version} 사용 위험".strip() if version else ""
        entry = next((e for e in self.data.versions.items if e.item.item_id == self.item.currentData()), None)
        return entry.item.content[:250] if entry else ""

    def _suggest_name(self):
        """사용자가 기준명을 직접 고치지 않았으면 제안으로 채운다. 판정 기준을 바꿔도 기준명을 비우지 않는다."""
        suggestion = self._suggestion()
        if not self.name.text().strip() or self.name.text() == self.suggested_name:
            self.name.setText(suggestion)
        self.suggested_name = suggestion

    def _versions_changed(self, *_):
        rule_type, fields, problem = self._rule()
        if self.criterion.currentData() == NOT_APPLIED:
            hint = "필수 Item이 포함된 버전 이상(누적 적용)을 쓰지 않는 설비가 위험 후보입니다."
        elif problem:
            hint = problem
        elif rule_type == "DIRECT_VERSION":
            hint = "위험 버전만 지정했습니다. 선택한 Item과 관계없이 이 버전을 쓰는 설비가 위험 후보입니다."
        else:
            end = self.end.currentText().split(" · ")[0] if fields["end_release_version_id"] is not None else ""
            span = f"{self.start.currentText().split(' · ')[0]}부터" + (f" {end} 전까지의 버전" if end else "의 버전(해제 버전 없음)")
            hint = f"{span} 중 선택한 Item이 든 버전을 쓰는 설비가 위험 후보입니다."
        self.criterion_hint.setText(hint)
        self._suggest_name()
        self._invalidate()

    def _item_changed(self, *_):
        entry = next((e for e in self.data.versions.items if e.item.item_id == self.item.currentData()), None)
        if self._rule()[0] != "DIRECT_VERSION":
            self._suggest_name()
        overview = self.data.catalog().overview(entry.item, ()) if entry else None
        first = next((f.first_included for f in overview.families if f.family_id == self.family.currentData()), None) if overview else None
        has_row = entry and any(row.item_id == entry.item.item_id for row in self.data.versions.original_rows)
        self.source_button.setText("원본 행 보기" if has_row else "출처 정보 보기")
        self.source_button.setEnabled(entry is not None)
        self.item_note.set_full_text(entry.item.content if entry else "선택한 계열에 일치하는 아이템이 없습니다.")
        self.item_meta.setVisible(entry is not None)
        if entry:
            self.item_family_chip.setText(self.family.currentText())
            self.item_first_chip.setText(f"최초 포함 버전 {first.version_raw if first else '미확인'}")
            self.item_first_chip.setToolTip("" if first else FIRST_HINT)
            self.item_source_chip.setText("원본 행 있음" if has_row else "원본 행 미보관")
            self.item_source_chip.set_tone("success" if has_row else "warning")
            self.item_source_chip.setToolTip("" if has_row else "원본 파일에서 직접 확인해야 합니다.")
            for chip in (self.item_family_chip, self.item_first_chip):
                chip.set_tone("neutral")
        self._invalidate()

    def _models_changed(self, *_):
        self.models.setText(" · ".join(a.text() for a in self.model_actions if a.isChecked()) or "대상 설비군 선택")
        self._invalidate()

    def _site_changed(self, *_):
        site = self.site.currentData()
        self.line.clear()
        self.line.addItem("전체 Line", None)
        self.line.setEnabled(site is not None)
        for line in sorted({r.line for r in self.data.records if r.site == site and r.line}):
            self.line.addItem(line, line)
        self._invalidate()

    def _update_editor(self, *_):
        editing = self.change_item.isChecked()
        self.change_item.setVisible(self.item_fixed)
        self.change_item.setText("선택 닫기" if editing else "아이템 변경")
        self.item_editor.setVisible(editing)
        self.item_search.setVisible(editing)

    def _criterion_changed(self, *_):
        self.version_fields.setVisible(self.criterion.currentData() == APPLIED)
        self._update_editor()
        self._item_changed()
        self._versions_changed()

    def values(self):
        """저장·미리보기 값. 판정 기준 두 가지를 입력에 따라 기존 rule_type 세 가지 중 하나로 바꾼다.
        모호한 조합이면 rule_type이 None이고 미리보기·등록을 하지 않는다."""
        rule_type, fields, _ = self._rule()
        values = dict(rule_type=rule_type, family_id=self.family.currentData(), risk_reason=self.name.text().strip(),
                      mapping_status="VERIFIED",
                      item_manager=dict(schema=1, name=self.name.text().strip(),
                                        category=CATEGORY_BY_RULE.get(rule_type, CATEGORY_BY_RULE["DEFECT_ITEM"]),
                                        memo=self.memo.text().strip(), model_ids=[a.data() for a in self.model_actions if a.isChecked()],
                                        site=self.site.currentData(), line=self.line.currentData()))
        values.update(fields)
        if rule_type in ("SAFETY_REQUIRED", "DEFECT_ITEM"):
            values["item_id"] = self.item.currentData()
        return values

    @staticmethod
    def _same_impact(first, second):
        """메모만 다른 입력은 같은 영향 계산으로 본다. 영향에 쓰이는 값은 모두 비교한다."""
        if first is None or second is None:
            return False
        def core(values):
            values = deepcopy(values)
            values["item_manager"]["memo"] = ""
            return values
        return core(first) == core(second)

    def _toggle_advanced(self, opened):
        self.advanced.setVisible(opened)
        self.advanced_toggle.setText("상세 설정 접기" if opened else "상세 설정 펼치기")

    def _invalidate(self, *_):
        self.request.cancel()
        self.preview_valid = False
        self.impact_table.hide()
        self.preview_values = None
        self.save_button.setEnabled(False)
        self.preview_button.hide()
        self.stats.hide()
        self._update_scope()
        self.preview.setText("현재 입력값으로 영향을 확인하고 있습니다.")
        self.next_step.setText("영향을 계산하고 있습니다. 잠시 후 등록할 수 있습니다.")
        if self.isVisible():
            self.preview_timer.start()

    def _update_scope(self):
        for chip, text in zip(self.scope_chips, (self.family.currentText() or "계열 미선택", self.site.currentText() or "전체 Site",
                                                 self.line.currentText() or "전체 Line")):
            chip.setText(text)
            chip.set_tone("info")

    def showEvent(self, event):
        super().showEvent(event)
        self.preview_timer.start(350)

    def hideEvent(self, event):
        self.preview_timer.stop()
        self.request.cancel()
        super().hideEvent(event)

    def _update_save_state(self, *_):
        ready = self.preview_valid and not self.submitted
        self.save_button.setEnabled(ready)
        if self.preview_valid:
            self.next_step.setText("")
            self.save_button.setToolTip("등록하면 선택한 Item·버전·설비군이 확인된 위험 기준으로 저장되고, 설비별 판정과 대시보드·히트맵 위험 집계에 반영됩니다.")


    def preview_impact(self):
        self.preview_timer.stop()
        if self.request.busy or self.submitted:
            return
        values = deepcopy(self.values())
        presentation = values["item_manager"]
        problem = ""
        if not presentation["name"]:
            problem = "위험 기준명을 입력하세요."
        elif not presentation["model_ids"]:
            problem = "대상 설비군을 선택하세요."
        elif values["rule_type"] is None:
            problem = self._rule()[2]
        elif values["rule_type"] != "DIRECT_VERSION" and not values.get("item_id"):
            problem = "관련 아이템을 선택하세요."
        elif values["rule_type"] == "DIRECT_VERSION" and not values.get("release_version_id"):
            problem = "대상 버전을 선택하세요."
        if problem:
            self.preview.setText(problem)
            self.next_step.setText(problem)
            return
        context, gateway = self.context_provider(), self.gateway
        self.preview_valid = False
        self.save_button.setEnabled(False)
        self.preview_values = values
        self.preview.setText("영향 계산 중…")
        self.preview_button.setEnabled(False)
        self.request.start(BackgroundRead(lambda: gateway.preview_risk_rule(values, context)))

    def _previewed(self, result, error):
        self.preview_button.setEnabled(True)
        if error is not None or result is None:
            self.preview_values = None
            self.preview.setText("미리보기를 불러오지 못했습니다.")
            self.preview_button.show()
            self.next_step.setText("연결을 확인한 뒤 다시 계산하세요.")
            return
        if not self._same_impact(self.preview_values, self.values()):
            return
        has_base = bool(result.base_date) and not result.problems
        self.stats.setVisible(has_base)
        if has_base:
            self.stat_values["risk"].setText(str(result.count(RiskStatus.RISK)))
            self.stat_values["unknown"].setText(str(result.count(RiskStatus.UNKNOWN)))
            self.stat_values["new"].setText(str(result.newly_risky))
            self.stat_values["hpa"].setText(str(result.hpa_excluded))
        if result.problems:
            self.preview.setText(" · ".join(result.problems))
        elif not has_base:
            self.preview.setText("설비 관측 자료가 없어 영향 설비를 계산할 수 없습니다.")
        else:
            self.preview.setText(f"관측일 {result.base_date}" + (
                f" · 대상 {result.target_count}대 · 위험 판정 없음 {result.target_count - len(result.equipment)}대"
                f" · 기존 위험 후보 {result.already_risky}대" if hasattr(result, "target_count") else ""))
        rows = []
        for impact in getattr(result, "equipment", ()):
            findings = impact.findings
            reasons = {}
            for finding in findings:
                reason = ("필수 아이템 미적용" if finding.basis is FindingBasis.SAFETY_NOT_APPLIED else finding.message)
                version = (finding.pm.version_raw or "버전 미확인") if finding.pm else ""
                pms = reasons.setdefault((version, reason), [])
                if finding.pm:
                    pms.append(finding.pm.pm_name or "공통 SW")
            details = [" · ".join(filter(None, (", ".join(pms), version, reason)))
                       for (version, reason), pms in reasons.items()]
            record = impact.record
            location = " · ".join(filter(None, (record.site, record.line)))
            status = ("판정 불가" if impact.status is RiskStatus.UNKNOWN else
                      "기존 위험 후보" if impact.before is RiskStatus.RISK else "신규 위험 후보")
            rows.append((f"{record.equipment_code}\n{location}", status, "\n".join(details)))
        self.impact_model.set_rows(rows)
        self.impact_table.resizeRowsToContents()
        self.impact_table.setFixedHeight(min(280, max(110, 50 + sum(self.impact_table.rowHeight(i) for i in range(min(6, len(rows)))))))
        self.impact_table.setVisible(bool(rows))
        self.preview_valid = result.saveable
        if result.problems:
            self.next_step.setText(result.problems[0])
        self._update_save_state()

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
        self.preview_timer.stop()
        self.request.cancel()
        super().reject()
