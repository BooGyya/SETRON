"""VER5 기간별 삭제 UI. SQL을 실행하지 않으며 명시적으로 주입된 삭제 서비스만 호출한다.

서비스는 목록의 revision과 keys를 재검증하고 전부 삭제하거나 전부 유지해야 한다.
기본 앱에는 삭제 서비스를 연결하지 않는다. 빈 목록·미연결·실패를 구분한다.
"""

from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLayout, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QProxyStyle, QSizePolicy, QStyle, QStyleFactory,
)

from setron.features.ai.schemas import ExtractKind
from setron.ui.appearance import ui_color
from setron.ui.combo_box import ComboBox
from setron.ui.components import Card, button, label
from setron.ui.import_widgets import PeriodDateField, import_combo_style, picture
from setron.ui.import_dialogs import ImportDialog
from setron.ui.query_panel import _WrapRow
from setron.ui.read_worker import BackgroundRead, ReadRequest
from setron.ui.theme import INK, LINE, PRIMARY, SECONDARY, font, icon, svg_size, set_widget_font


class _DeleteFilterRow(QWidget):
    """보이는 필드만 왼쪽부터 배치한다. 숨긴 필드의 빈 열을 남기지 않는다."""

    def __init__(self, equipment_fields, release_fields):
        super().__init__()
        self.equipment_fields, self.release_fields = equipment_fields, release_fields
        self.fields = ()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
        # 숨긴 삭제 탭의 이전 열 너비가 가져오기 화면의 최소 폭을 강제하지 않게 한다.
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        for field in dict.fromkeys((*equipment_fields, *release_fields)):
            field.setParent(self)
        self.show_kind(True)

    def show_kind(self, equipment):
        active = self.equipment_fields if equipment else self.release_fields
        if self.fields == active:
            return
        for field in dict.fromkeys((*self.equipment_fields, *self.release_fields)):
            self.grid.removeWidget(field)
            field.setVisible(field in active)
        self.fields = active
        self._arrange()

    def _arrange(self):
        needed = sum(field.maximumWidth() for field in self.fields) + 16 * (len(self.fields) - 1)
        two_columns = sum(max(field.maximumWidth() for field in self.fields[index::2])
                          for index in range(min(2, len(self.fields)))) + 16
        columns = len(self.fields) if self.width() >= needed else 2 if self.width() >= two_columns else 1
        for column in range(6):
            self.grid.setColumnStretch(column, 0)
        for index, field in enumerate(self.fields):
            self.grid.removeWidget(field)
            self.grid.addWidget(field, index // columns, index % columns, Qt.AlignmentFlag.AlignLeft)
        self.grid.setColumnStretch(columns, 1)
        self.updateGeometry()

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())

    def resizeEvent(self, event):
        self._arrange()
        super().resizeEvent(event)


class _DeleteCheckStyle(QProxyStyle):
    """선택 체크의 도형만 그린다. 키보드·포커스·클릭은 기존 Qt 체크박스가 처리한다."""

    def __init__(self, parent):
        super().__init__(QStyleFactory.create("Fusion"))
        self.setParent(parent)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight):
            return 18
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            return super().drawPrimitive(element, option, painter, widget)
        center = option.rect.center()
        partial = bool(option.state & QStyle.StateFlag.State_NoChange)
        checked = bool(option.state & QStyle.StateFlag.State_On) or partial
        painter.save()
        if not option.state & QStyle.StateFlag.State_Enabled:
            painter.setOpacity(0.4)
        painter.setPen(ui_color("#c23345" if checked else "#b8c0d0"))
        painter.setBrush(ui_color("#c23345" if checked else "white"))
        painter.drawRoundedRect(QRectF(center.x() - 8, center.y() - 8, 16, 16), 3, 3)
        if partial:
            painter.setPen(ui_color("white"))
            painter.drawLine(center.x() - 4, center.y(), center.x() + 4, center.y())
        elif checked:
            size = svg_size("ai-ver5/330d5.svg")
            painter.drawPixmap(center.x() - size.width() // 2, center.y() - size.height() // 2,
                               icon("ai-ver5/330d5.svg").pixmap(size))
        painter.restore()


@dataclass(frozen=True)
class DeletionBatch:
    """저장 묶음. key는 서비스의 불변 식별자이며 표시 날짜로 대체하지 않는다."""

    key: str
    kind: ExtractKind
    day: date | None
    site: str | None
    family: str | None
    sheet: str
    equipment_count: int
    row_count: int
    saved_at: str
    method: str
    deletable: bool = True

    def __post_init__(self):
        if not isinstance(self.key, str) or not self.key or not isinstance(self.kind, ExtractKind):
            raise ValueError("삭제 묶음 식별자가 올바르지 않습니다.")
        if self.kind is ExtractKind.EQUIPMENT_STATUS and not isinstance(self.day, date):
            raise ValueError("현황표 삭제에는 확인된 기준일이 필요합니다.")
        if self.kind is ExtractKind.RELEASE_NOTE and not self.family:
            raise ValueError("릴리즈 삭제에는 확인된 설비군/계열이 필요합니다.")
        if self.day is not None and type(self.day) is not date:
            raise ValueError("기준일은 날짜여야 합니다.")
        if any(value is not None and not isinstance(value, str) for value in (self.site, self.family)):
            raise ValueError("Site·계열 이름 형식이 올바르지 않습니다.")
        if any(not isinstance(value, str) for value in (self.sheet, self.saved_at, self.method)):
            raise ValueError("저장 묶음 표시 정보 형식이 올바르지 않습니다.")
        if any(type(v) is not int or v < 0 for v in (self.equipment_count, self.row_count)):
            raise ValueError("삭제 대수·행 수는 0 이상의 정수여야 합니다.")
        if not isinstance(self.deletable, bool):
            raise ValueError("삭제 가능 여부는 참 또는 거짓이어야 합니다.")


@dataclass(frozen=True)
class DeletionCatalog:
    revision: str
    batches: tuple[DeletionBatch, ...]
    source_label: str

    def __post_init__(self):
        if (not isinstance(self.revision, str) or not self.revision or not isinstance(self.source_label, str)
                or not isinstance(self.batches, tuple) or any(not isinstance(b, DeletionBatch) for b in self.batches)):
            raise ValueError("삭제 목록 형식이 올바르지 않습니다.")
        if len({b.key for b in self.batches}) != len(self.batches):
            raise ValueError("삭제 묶음 식별자가 중복되었습니다.")


@dataclass(frozen=True)
class DeletionPlan:
    revision: str
    keys: tuple[str, ...]
    kind: ExtractKind
    start: date | None
    end: date | None
    site: str | None
    family: str | None

    def __post_init__(self):
        if not isinstance(self.revision, str) or not self.revision or not isinstance(self.kind, ExtractKind):
            raise ValueError("삭제 요청의 기준 목록이 필요합니다.")
        if (not isinstance(self.keys, tuple) or not self.keys or any(not isinstance(k, str) or not k for k in self.keys)
                or len(set(self.keys)) != len(self.keys)):
            raise ValueError("삭제 대상 ID는 비어 있거나 중복될 수 없습니다.")
        if self.kind is ExtractKind.EQUIPMENT_STATUS:
            if type(self.start) is not date or type(self.end) is not date or self.start > self.end:
                raise ValueError("삭제 기간이 올바르지 않습니다.")
        elif self.start is not None or self.end is not None or not self.family:
            raise ValueError("릴리즈 삭제는 기간이 아니라 계열을 지정해야 합니다.")


@dataclass(frozen=True)
class DeletionResult:
    """success는 요청한 keys 전체의 확정 삭제만 의미한다. unknown은 재시도를 금지한다."""

    status: str
    deleted_keys: tuple[str, ...] = ()
    message: str = ""
    revision: str = ""

    def __post_init__(self):
        if self.status not in {"success", "failed", "unknown"}:
            raise ValueError("삭제 결과 상태가 올바르지 않습니다.")
        if not isinstance(self.deleted_keys, tuple) or any(not isinstance(key, str) for key in self.deleted_keys):
            raise ValueError("삭제 결과 식별자 형식이 올바르지 않습니다.")
        if not isinstance(self.message, str) or not isinstance(self.revision, str):
            raise ValueError("삭제 결과 안내 형식이 올바르지 않습니다.")


DANGER_STYLE = ("QPushButton {background:#c23345;color:white;border:1px solid #c23345;}"
                "QPushButton:disabled {background:#f3dce0;color:#a97982;border-color:#f3dce0;}"
                "QPushButton:focus {border:2px solid #142033;}")
DELETE_HEADERS = ("", "기준일", "Site", "시트", "설비 / 행", "저장일", "저장 방식", "상태")
RELEASE_HEADERS = ("", "SW 계열", "Site", "입력 작업 시트", "버전 수", "저장일", "저장 방식", "상태")


class DeleteConfirmDialog(ImportDialog):
    """실제 선택 범위를 다시 표시하고 ‘삭제’의 정확한 입력과 별도 클릭을 요구한다."""

    def __init__(self, plan, batches, *, connected, parent=None):
        super().__init__("저장된 데이터 삭제 확인", parent)
        self.heading.hide()
        body = self.body
        header = QHBoxLayout()
        icon_slot = QFrame()
        icon_slot.setFixedSize(40, 40)
        icon_slot.setStyleSheet("background:#f8e7e9;border-radius:10px;")
        slot = QHBoxLayout(icon_slot)
        slot.setContentsMargins(0, 0, 0, 0)
        slot.addWidget(picture("116c6.svg"), 0, Qt.AlignmentFlag.AlignCenter)
        header.addWidget(icon_slot)
        count = len({b.day for b in batches}) if plan.kind is ExtractKind.EQUIPMENT_STATUS else len(batches)
        header.addWidget(label(f"{'기준일' if plan.kind is ExtractKind.EQUIPMENT_STATUS else '릴리즈 묶음'} {count}개를 삭제할까요?", 18, medium=True), 1)
        body.addLayout(header)
        caption = (f"기간 {plan.start} ~ {plan.end}" if plan.kind is ExtractKind.EQUIPMENT_STATUS
                   else f"릴리즈 노트 · {plan.family or '선택한 설비군/계열'}")
        body.addWidget(label(caption + f" · Site {plan.site or '전체'}", 12, color=SECONDARY, wrap=True))
        details = " · ".join(f"{b.day or b.family} ({b.equipment_count}대 · {b.row_count}행)" for b in batches)
        self.details = label("삭제: " + details, 12, wrap=True)
        body.addWidget(self.details)
        body.addWidget(label("삭제 후에는 되돌릴 수 없습니다. 원본 Excel 파일은 변경하지 않습니다.", 12, color="#c23345", wrap=True))
        self.connection_notice = label("삭제 서비스 미연결 · 실제 삭제는 실행할 수 없습니다." if not connected else "", 12, wrap=True)
        self.connection_notice.setVisible(not connected)
        body.addWidget(self.connection_notice)
        body.addWidget(label("확인을 위해 아래 칸에 ‘삭제’를 입력하세요", 12))
        self.phrase = QLineEdit()
        self.phrase.setAccessibleName("삭제 확인 문구")
        self.phrase.setPlaceholderText("삭제")
        self.phrase.setMinimumHeight(40)
        self.phrase.setStyleSheet("QLineEdit {background:white;border:1px solid #c23345;border-radius:8px;padding:0 10px;}"
                                 "QLineEdit:focus {border:2px solid #c23345;}")
        body.addWidget(self.phrase)
        row = QHBoxLayout()
        row.addStretch()
        cancel, self.delete_button = button("취소"), button("삭제")
        self.delete_button.setStyleSheet(DANGER_STYLE)
        self.delete_button.setEnabled(False)
        self.delete_button.setAutoDefault(False)
        cancel.setDefault(True)
        self.phrase.textChanged.connect(lambda text: self.delete_button.setEnabled(connected and text == "삭제"))
        cancel.clicked.connect(self.reject)
        self.delete_button.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.delete_button)
        body.addLayout(row)


class ImportDeletePage(QWidget):
    """종류·Site·기간/계열 선택 → 조건에 맞는 가져온 엑셀만 목록에 보임 → 지울 것을 직접 체크 → 영향 확인 → 명시 확인.

    11-11 4차: 목록은 화면에 들어올 때 읽어 두고, 조건을 고른 뒤 [검색]을 눌러야 그 조건의 파일이 2번 목록에 보인다.
    조건을 바꾸면 목록을 비우고 다시 [검색]을 기다린다. 자동 선택은 하지 않고, 기간을 비우면 기간 제한이 없다.
    기본 상태는 미연결이다.
    """

    backup_requested = Signal()
    cancel_requested = Signal()
    deleted = Signal(object)
    busy_changed = Signal()

    def __init__(self, gateway=None):
        super().__init__()
        self.setObjectName("canvas")
        self.gateway = gateway
        self.catalog = None
        self.phase = "idle"
        self.selected = set()
        self.checks = {}
        self._check_style = _DeleteCheckStyle(self)
        self.open_dialog = lambda dialog: dialog.exec() == QDialog.DialogCode.Accepted
        self.request = ReadRequest(self)
        self.request.completed.connect(self._completed)
        self.pending_plan = None
        self.applied = None  # [검색]을 누른 때의 조건 (종류, Site, 계열, 시작일, 종료일)
        self._search_after_load = False
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        self.notice = label("", 13, color=SECONDARY, wrap=True)
        self.notice.hide()
        body.addWidget(self.notice)
        filters = Card(padding=24)
        self.filters_card = filters
        filters.setProperty("outlined", True)
        self.filters_title = label("1. 삭제할 자료와 기간 선택", 20, medium=True)
        filters.body.addWidget(self.filters_title)
        self.kind_combo, self.site_combo, self.family_combo = ComboBox(), ComboBox(), ComboBox()
        self.kind_combo.addItem("설비 현황표", ExtractKind.EQUIPMENT_STATUS)
        self.kind_combo.addItem("릴리즈 노트", ExtractKind.RELEASE_NOTE)
        self.site_combo.addItem("전체", None)
        self.family_combo.addItem(self._family_placeholder, None)
        self.start, self.end = PeriodDateField(), PeriodDateField()
        from setron.ui.versions import _field
        self.start_box, self.end_box = _field("시작일", self.start), _field("종료일", self.end)
        self.site_box, self.family_box = _field("Site", self.site_combo), _field("설비군/계열", self.family_combo)
        self.kind_box = _field("자료 종류", self.kind_combo)
        self.end_group = QWidget()
        date_end = QHBoxLayout(self.end_group)
        date_end.setContentsMargins(0, 0, 0, 0)
        date_end.setSpacing(12)
        separator = label("~", 18, medium=True, color=SECONDARY)
        separator.setFixedSize(12, 42)
        date_end.addWidget(separator, 0, Qt.AlignmentFlag.AlignBottom)
        date_end.addWidget(self.end_box)
        for field, width in ((self.kind_box, 200), (self.site_box, 160), (self.start_box, 190),
                             (self.end_group, 214), (self.family_box, 200)):
            field.setFixedWidth(width)
        self.fields_row = _DeleteFilterRow((self.kind_box, self.site_box, self.start_box, self.end_group),
                                           (self.kind_box, self.family_box))
        filters.body.addWidget(self.fields_row)
        self.quick_choices = QWidget()
        quick_layout = QHBoxLayout(self.quick_choices)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        quick_layout.setSpacing(8)
        self.quick_label = label("빠른 선택", 12, medium=True, color=SECONDARY)
        quick_layout.addWidget(self.quick_label, 0, Qt.AlignmentFlag.AlignTop)
        self.quick_buttons = []
        self.quick_group = QButtonGroup(self)
        for text, mode in (("가장 최근 기준일", "latest"), ("최근 1개월", "month"), ("최근 3개월", "quarter"), ("직접 선택", "manual")):
            item = button(text)
            item.setCheckable(True)
            set_widget_font(item, font(12, medium=True))
            item.setFixedHeight(32)
            item.setStyleSheet(f"QPushButton {{background:white;color:{INK};border:1px solid {LINE};"
                              "border-radius:16px;padding:0 12px;min-height:30px;}"
                              f"QPushButton:checked {{background:#ebeffd;color:{PRIMARY};border-color:{PRIMARY};}}"
                              f"QPushButton:focus {{border:2px solid {PRIMARY};}}")
            self.quick_group.addButton(item)
            item.clicked.connect(lambda checked=False, value=mode: self.quick_range(value))
            self.quick_buttons.append(item)
        self.quick_buttons[-1].setChecked(True)
        quick_wrap = _WrapRow(self.quick_buttons, 120)
        quick_wrap.grid.setSpacing(8)
        quick_layout.addWidget(quick_wrap, 1)
        self.search_button = button("검색", primary=True)
        self.search_button.setFixedHeight(32)
        self.search_button.setMinimumWidth(88)
        self.search_button.setAccessibleName("고른 조건으로 가져온 엑셀 검색")
        self.search_button.clicked.connect(self.search)
        quick_layout.addWidget(self.search_button, 0, Qt.AlignmentFlag.AlignTop)
        self.quick_label.setContentsMargins(0, 8, 0, 0)
        filters.body.addWidget(self.quick_choices)
        self.kind_notice = label("릴리즈 노트는 기간 대신 확인된 설비군/계열을 선택합니다.", 12, color=SECONDARY, wrap=True)
        filters.body.addWidget(self.kind_notice)
        self.range_message = label("", 12, color=SECONDARY, wrap=True)
        self.range_message.hide()
        filters.body.addWidget(self.range_message)
        # 목록은 화면에 들어올 때 읽고 조건을 바꾸면 바로 걸러 보인다 (11-11, 조회 버튼 없음). 호환용으로만 남긴다.
        self.reload_button = button("저장된 자료 조회")
        self.reload_button.clicked.connect(self.reload)
        self.reload_button.setParent(self)
        self.reload_button.hide()
        body.addWidget(filters)
        listing = Card(padding=24)
        listing.setProperty("outlined", True)
        self.listing_title = label("2. 지울 엑셀 선택", 20, medium=True)
        listing.body.addWidget(self.listing_title)
        self.table = QTableWidget(0, len(DELETE_HEADERS))
        # 스타일을 사용하는 헤더/행 체크박스가 먼저 정리되도록 테이블이 소유한다.
        self._check_style.setParent(self.table)
        self.table.setHorizontalHeaderLabels(DELETE_HEADERS)
        self.table.setAccessibleName("삭제 대상 저장 묶음")
        self.table.setMinimumWidth(0)
        self.table.setMinimumHeight(240)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 44)
        self.select_all = QCheckBox(self.table.horizontalHeader())
        self.select_all.setTristate(True)
        self.select_all.setStyle(self._check_style)
        self.select_all.setAccessibleName("삭제 가능한 자료 전체 선택")
        self.select_all.setToolTip("현재 기간·Site·계열 안의 자료만 선택합니다.")
        self.select_all.clicked.connect(self.toggle_all)
        self.table.horizontalHeader().geometriesChanged.connect(self._position_select_all)
        self.table.horizontalHeader().sectionResized.connect(self._position_select_all)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setStyleSheet("QTableWidget {background:white;border:1px solid #dee0e5;border-radius:8px;}"
                                "QHeaderView::section {background:#f4f5f8;border:0;padding:8px;}"
                                "QTableWidget::item {padding:6px 8px;}")
        listing.body.addWidget(self.table)
        self.empty = label("가져온 엑셀 목록을 읽고 있습니다.", 13, color=SECONDARY, wrap=True)
        listing.body.addWidget(self.empty)
        body.addWidget(listing)
        impact = Card(padding=24)
        self.impact_card = impact
        impact.setProperty("outlined", True)
        impact.body.addWidget(label("3. 삭제 영향 확인", 20, medium=True))
        self.counts = label("삭제할 기준일 — · 삭제할 현황표 행 — · 남는 기준일 —", 16, medium=True, wrap=True)
        self.counts.setParent(self)
        self.counts.hide()  # 기존 요약 계약은 보존하고 화면에는 Figma의 세 지표 묶음으로 표시한다.
        self.impact_stats = QWidget()
        stats = QHBoxLayout(self.impact_stats)
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(28)
        self.stat_boxes, self.stat_titles, self.stat_values = [], [], []
        for title in ("삭제할 기준일", "삭제할 현황표 행", "남는 기준일"):
            box = QWidget()
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            caption = label(title, 12, color=SECONDARY)
            value = label("—", 20, medium=True, color=INK if title == "남는 기준일" else "#c23345")
            layout.addWidget(caption)
            layout.addWidget(value)
            stats.addWidget(box)
            self.stat_boxes.append(box)
            self.stat_titles.append(caption)
            self.stat_values.append(value)
        stats.addStretch()
        impact.body.addWidget(self.impact_stats)
        self.effect = label("삭제할 자료를 먼저 선택해 주세요.", 13, wrap=True)
        impact.body.addWidget(self.effect)
        self.backup_tip = QFrame()
        self.backup_tip.setObjectName("deleteBackupTip")
        self.backup_tip.setStyleSheet("QFrame#deleteBackupTip {background:#f6f0e0;border:0;border-radius:8px;}")
        backup_row = QHBoxLayout(self.backup_tip)
        backup_row.setContentsMargins(14, 10, 14, 10)
        backup_row.setSpacing(10)
        self.warning = label("삭제한 자료는 되돌릴 수 없어요. 필요하면 먼저 DB 내보내기로 백업하세요", 13, color="#9e6b00", wrap=True)
        self.warning.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        backup_row.addWidget(self.warning, 1)
        self.backup_button = button("DB 내보내기")
        self.backup_button.setEnabled(False)
        self.backup_button.setToolTip("DB 내보내기 공급자가 연결되면 사용할 수 있습니다.")
        self.backup_button.clicked.connect(self.backup_requested.emit)
        self.backup_button.setFixedHeight(40)
        backup_row.addWidget(self.backup_button)
        impact.body.addWidget(self.backup_tip)
        self.cancel_button, self.delete_button = button("취소"), button("선택한 자료 삭제")
        self.delete_button.setStyleSheet(DANGER_STYLE)
        self.cancel_button.clicked.connect(lambda: (self.clear_selection(), self.cancel_requested.emit()))
        self.delete_button.clicked.connect(self.confirm_delete)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.delete_button)
        impact.body.addLayout(footer)
        body.addWidget(impact)
        self.step_cards = (filters, listing, impact)
        for card in (filters, listing, impact):
            card.body.setContentsMargins(28, 24, 28, 24)
            card.setStyleSheet("QFrame#card {background:white;border:1px solid #dee0e5;border-radius:16px;}")
        body.addStretch()
        for control, caption in ((self.kind_combo, "삭제할 자료 종류"), (self.site_combo, "삭제할 Site"),
                                 (self.family_combo, "삭제할 설비군/계열"), (self.start, "삭제 시작일"), (self.end, "삭제 종료일")):
            control.setAccessibleName(caption)
            control.setProperty("queryField", True)
            control.setFixedHeight(42)
            if isinstance(control, QComboBox):
                control.setStyleSheet(import_combo_style())
                control.currentIndexChanged.connect(self.apply_filter)
            else:
                control.textChanged.connect(self.apply_filter)
        self.apply_filter()

    @property
    def busy(self):
        return self.phase in {"loading", "deleting"}

    @property
    def kind(self):
        # Qt QVariant은 str 기반 Enum을 문자열로 돌려줄 수 있다.
        return ExtractKind(self.kind_combo.currentData())

    @property
    def _release_deletion_supported(self):
        return getattr(self.gateway, "release_deletion_supported", True) is not False

    @property
    def _family_placeholder(self):
        return "설비군/계열 선택" if self._release_deletion_supported else "전체"

    @property
    def viewing_releases(self):
        return self.kind is ExtractKind.RELEASE_NOTE and not self._release_deletion_supported

    def _set_notice(self, text):
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def reload(self):
        if self.busy or self.phase == "unknown":
            return False
        if self.gateway is None or not callable(getattr(self.gateway, "list_batches", None)):
            self._set_notice("")
            return False
        self.catalog = None
        self.selected.clear()
        self.phase = "loading"
        self._set_notice("저장된 자료 목록을 읽고 있습니다…")
        self._render()
        self.busy_changed.emit()
        self.request.start(BackgroundRead(self.gateway.list_batches))
        return True

    def invalidate_catalog(self):
        """가져오기 저장 후 다음 진입에서 목록을 다시 읽는다."""
        if self.phase == "idle":
            self.catalog = None
            self.selected.clear()

    def set_catalog(self, catalog):
        if self.busy or self.phase == "unknown":
            return False
        if not isinstance(catalog, DeletionCatalog):
            raise ValueError("삭제 목록 형식이 올바르지 않습니다.")
        self.catalog = catalog
        self.selected.clear()
        self.phase = "idle"
        for combo, values in ((self.site_combo, sorted({b.site for b in catalog.batches if b.site})),
                              (self.family_combo, sorted({b.family for b in catalog.batches if b.family}))):
            combo.blockSignals(True)
            previous = combo.currentData()
            combo.clear()
            combo.addItem("전체" if combo is self.site_combo else self._family_placeholder, None)
            for value in values:
                combo.addItem(value, value)
            index = combo.findData(previous)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)
        self._set_notice(catalog.source_label)
        if self._search_after_load:
            self._search_after_load = False
            self.applied = self._filters() if self._valid_filters() else None
        self.apply_filter()
        return True

    def _filters(self):
        return (self.kind, self.site_combo.currentData(), self.family_combo.currentData(), self.start.text(), self.end.text())

    def _valid_filters(self):
        return not (self.kind is ExtractKind.EQUIPMENT_STATUS and (self.start.text() or self.end.text()) and self._dates() is None)

    def search(self):
        """고른 조건으로 2번 목록을 보인다. 목록을 아직 읽지 않았으면 읽은 뒤 보인다."""
        if self.busy or self.phase == "unknown":
            return False
        if not self._valid_filters():
            return False
        if self.catalog is None:
            self._search_after_load = True
            return self.reload()
        self.applied = self._filters()
        self.selected.clear()
        self._render()
        return True

    def _dates(self):
        try:
            start, end = date.fromisoformat(self.start.text()), date.fromisoformat(self.end.text())
            if start > end:
                raise ValueError()
            return start, end
        except ValueError:
            return None

    def quick_range(self, mode):
        if self.busy or self.phase == "unknown":
            return
        if mode == "manual":
            self.quick_buttons[-1].setChecked(True)
            self.start.setFocus()
            return
        days = [b.day for b in self.catalog.batches if b.day and b.kind is ExtractKind.EQUIPMENT_STATUS
                and (self.site_combo.currentData() is None or b.site == self.site_combo.currentData())] if self.catalog else []
        if not days:
            self._set_notice("선택한 Site에 저장된 기준일이 없습니다.")
            return
        end = max(days)
        from PySide6.QtCore import QDate
        first = end if mode == "latest" else QDate(end.year, end.month, end.day).addMonths(-1 if mode == "month" else -3).toPython()
        self.start.setText(first.isoformat())
        self.end.setText(end.isoformat())
        self.quick_buttons[("latest", "month", "quarter").index(mode)].setChecked(True)

    def _applied_dates(self):
        _, _, _, start, end = self.applied
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            return None
        return (first, last) if first <= last else None

    def matches(self, batch):
        """[검색]한 종류 · Site · 기간(비우면 전체) · 계열에 맞는 묶음인가 (2번 목록에 보이는 것). 검색 전에는 없다."""
        if self.applied is None:
            return False
        kind, site, family, start, end = self.applied
        if batch.kind is not kind:
            return False
        if batch.kind is ExtractKind.RELEASE_NOTE:
            return family is None or batch.family == family
        if site is not None and batch.site != site:
            return False
        if not (start or end):
            return True
        days = self._applied_dates()
        return bool(days and days[0] <= batch.day <= days[1])

    def eligible(self, batch):
        if not batch.deletable or self.viewing_releases or not self.matches(batch):
            return False
        return batch.kind is not ExtractKind.RELEASE_NOTE or self.applied[2] is not None

    def apply_filter(self, *_):
        equipment = self.kind is ExtractKind.EQUIPMENT_STATUS
        viewing = self.viewing_releases
        if self.sender() in (self.start, self.end):
            self.quick_buttons[-1].setChecked(True)
        invalid = equipment and bool(self.start.text() or self.end.text()) and self._dates() is None
        self.range_message.setText("시작일·종료일을 유효한 YYYY-MM-DD 날짜로 입력하고, 시작일이 종료일보다 늦지 않은지 확인하세요." if invalid else "")
        self.range_message.setVisible(invalid)
        for widget in (self.start_box, self.end_box, self.site_box):
            widget.setVisible(equipment)
        self.fields_row.show_kind(equipment)
        self.quick_choices.setVisible(equipment)
        self.kind_notice.setText("이 탭에서 릴리즈 버전은 조회만 할 수 있습니다. "
                                 "버전과 Item 상세는 ‘버전 관리 > 릴리즈 · Item’ 또는 ‘아이템 매니저’에서 확인하세요."
                                 if viewing else "릴리즈 노트는 기간 대신 확인된 설비군/계열을 선택합니다.")
        self.kind_notice.setVisible(not equipment)
        self.filters_title.setText("1. 조회할 릴리즈 계열 선택" if viewing else "1. 삭제할 자료와 기간 선택")
        if viewing:
            self.listing_title.setText("2. 저장된 릴리즈 버전")
        else:
            self.listing_title.setText("2. 지울 엑셀 선택" if equipment else "2. 지울 릴리즈 묶음 선택")
        self.kind_combo.setAccessibleName("자료 종류" if viewing else "삭제할 자료 종류")
        self.family_combo.setAccessibleName("조회할 SW 계열" if viewing else "삭제할 설비군/계열")
        self.impact_card.setVisible(not viewing)
        self.table.setHorizontalHeaderLabels(RELEASE_HEADERS if viewing else DELETE_HEADERS)
        self.table.setAccessibleName("저장된 릴리즈 버전" if viewing else "삭제 대상 저장 묶음")
        self.table.setColumnHidden(0, viewing)
        self.table.setColumnHidden(2, not equipment)  # 릴리즈 노트에는 Site가 없다
        self.select_all.setVisible(not viewing)
        if not self.busy and self.phase != "unknown":
            # 조건을 바꾸면 목록을 비우고 다시 [검색]을 기다린다 (11-11 4차). 자동으로 고르지 않는다.
            if self.applied is not None and self.applied != self._filters():
                self.applied = None
            self.selected = {b.key for b in self.catalog.batches if b.key in self.selected and self.eligible(b)} if self.catalog else set()
        self._render()

    def _selected_batches(self):
        return tuple(b for b in self.catalog.batches if b.key in self.selected and self.eligible(b)) if self.catalog else ()

    def _toggle(self, key, checked):
        if self.busy or self.phase == "unknown":
            return
        self.selected.add(key) if checked else self.selected.discard(key)
        self._render()

    def clear_selection(self):
        if not self.busy and self.phase != "unknown":
            self.selected.clear()
            self._render()

    def _position_select_all(self, *_):
        header = self.table.horizontalHeader()
        self.select_all.setGeometry(header.sectionViewportPosition(0) + (header.sectionSize(0) - 18) // 2,
                                    (header.height() - 18) // 2, 18, 18)

    def toggle_all(self, *_):
        if self.busy or self.phase == "unknown" or self.catalog is None:
            return
        eligible = {b.key for b in self.catalog.batches if self.eligible(b)}
        self.selected = set() if eligible and eligible <= self.selected else eligible
        self._render()

    def _render(self):
        locked = self.busy or self.phase == "unknown"
        for widget in (self.kind_combo, self.site_combo, self.family_combo, self.start, self.end,
                       self.reload_button, self.search_button, self.cancel_button, *self.quick_buttons):
            widget.setEnabled(not locked)
        selected = self._selected_batches()
        batches = tuple(b for b in self.catalog.batches if self.matches(b)) if self.catalog else ()
        equipment = self.kind is ExtractKind.EQUIPMENT_STATUS
        viewing = self.viewing_releases
        if equipment:
            batches = tuple(sorted(batches, key=lambda b: (b.day, b.site or ""), reverse=True))
        eligible_keys = {b.key for b in batches if self.eligible(b)}
        self.select_all.blockSignals(True)
        self.select_all.setCheckState(Qt.CheckState.Checked if eligible_keys and eligible_keys <= self.selected else
                                      Qt.CheckState.PartiallyChecked if eligible_keys & self.selected else Qt.CheckState.Unchecked)
        self.select_all.setEnabled(bool(eligible_keys) and not locked)
        self.select_all.blockSignals(False)
        self._position_select_all()
        self.table.setRowCount(len(batches))
        self.checks = {}
        for r, batch in enumerate(batches):
            if not viewing:
                check = QCheckBox()
                check.setStyle(self._check_style)
                check.setAccessibleName(f"{batch.day or batch.family} · {batch.site or ''} · {batch.sheet} 삭제 선택")
                check.setChecked(batch in selected)
                check.setEnabled(not locked and batch.key in eligible_keys)
                check.toggled.connect(lambda value, key=batch.key: self._toggle(key, value))
                wrapper = QWidget()
                check_row = QHBoxLayout(wrapper)
                check_row.setContentsMargins(4, 0, 4, 0)
                check_row.addWidget(check, 0, Qt.AlignmentFlag.AlignCenter)
                if batch in selected:
                    wrapper.setStyleSheet("background:#fcf0f2;")
                self.table.setCellWidget(r, 0, wrapper)
                self.checks[batch.key] = check
            status = "삭제 예정" if batch in selected else "유지"
            if viewing or not batch.deletable:
                status = "조회 전용"
            count = f"{batch.row_count}개" if viewing else f"{batch.equipment_count}대 / {batch.row_count}행"
            texts = (str(batch.day or batch.family), batch.site or "—", batch.sheet, count, batch.saved_at or "미확인",
                     batch.method or "미확인", status)
            for c, text in enumerate(texts, 1):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if batch in selected:
                    item.setBackground(ui_color("#fcf0f2"))
                self.table.setItem(r, c, item)
        self.empty.setVisible(not batches)
        missing = "저장된 릴리즈 버전이 없습니다." if viewing else "고른 조건에 맞는 가져온 엑셀이 없습니다. Site나 기간을 바꿔 보세요."
        self.empty.setText("조건을 고른 뒤 [검색]을 누르면 지울 수 있는 가져온 엑셀이 보입니다." if self.applied is None
                           else missing if self.catalog else "가져온 엑셀 목록을 읽고 있습니다.")
        # 남는 기준일은 목록에 보이는 것만이 아니라 같은 종류의 저장 자료 전체에서 센다.
        everything = tuple(b for b in self.catalog.batches if b.kind is self.kind) if self.catalog else ()
        dates = {b.day for b in everything if b.day}
        remaining = dates - {b.day for b in selected}
        # 같은 날짜의 다른 Site 자료가 남으면 기준일도 남는다.
        remaining |= {b.day for b in everything if b.day and b not in selected}
        if self.catalog:
            self.counts.setText((f"삭제할 기준일 {len({b.day for b in selected})}개 · 선택 묶음 {len(selected)}개 · "
                                 f"삭제할 현황표 행 {sum(b.row_count for b in selected)}행 · 남는 기준일 {len(remaining)}개") if equipment else
                                f"선택한 릴리즈 묶음 {len(selected)}개 · 삭제할 행 {sum(b.row_count for b in selected)}행")
        else:
            self.counts.setText("삭제할 기준일 — · 삭제할 현황표 행 — · 남는 기준일 —" if equipment else
                                "삭제할 릴리즈 묶음 — · 삭제할 행 —")
        self.impact_stats.setAccessibleName(self.counts.text())
        self.stat_titles[0].setText("삭제할 기준일" if equipment else "삭제할 릴리즈 묶음")
        self.stat_titles[1].setText("삭제할 현황표 행" if equipment else "삭제할 행")
        self.stat_boxes[2].setVisible(equipment)
        amounts = ((f"{len({b.day for b in selected}) if equipment else len(selected)}개",
                    f"{sum(b.row_count for b in selected)}행", f"{len(remaining)}개" if equipment else "—")
                   if self.catalog else ("—", "—", "—"))
        for widget, text in zip(self.stat_values, amounts):
            widget.setText(text)
        self.effect.setText(("" if equipment else
                             "• 선택한 설비군/계열의 릴리즈·Item 연결과 판정에 영향을 줍니다.\n"
                             "• 위험 근거와 목표 N의 처리 여부는 삭제 서비스가 재검증해야 합니다. 원본 Excel은 유지합니다."))
        self.effect.setVisible(bool(self.effect.text()))
        self.delete_button.setText(f"선택한 {'기준일' if equipment else '릴리즈 묶음'} {len({b.day for b in selected}) if equipment else len(selected)}개 삭제")
        self.delete_button.setEnabled(bool(selected) and not locked)
        self.backup_button.setEnabled(self.property("backupAvailable") is True and not locked)

    def plan(self):
        selected = self._selected_batches()
        if not selected or self.catalog is None:
            return None
        equipment = self.kind is ExtractKind.EQUIPMENT_STATUS
        # 기간을 비워 두면 고른 묶음의 첫·마지막 기준일을 삭제 기간으로 쓴다.
        days = (self._dates() or (min(b.day for b in selected), max(b.day for b in selected))) if equipment else (None, None)
        return DeletionPlan(self.catalog.revision, tuple(b.key for b in selected), self.kind,
                            *days, self.site_combo.currentData() if equipment else None,
                            self.family_combo.currentData() if not equipment else None)

    def confirm_delete(self):
        if self.busy or self.phase == "unknown":
            return False
        plan = self.plan()
        if plan is None:
            return False
        connected = callable(getattr(self.gateway, "delete", None))
        dialog = DeleteConfirmDialog(plan, self._selected_batches(), connected=connected, parent=self)
        accepted = self.open_dialog(dialog)
        confirmed = accepted and dialog.phrase.text() == "삭제" and connected
        dialog.deleteLater()
        if not confirmed:
            return False
        if self.plan() != plan:
            self._set_notice("목록 또는 선택 조건이 바뀌었습니다. 삭제 범위를 다시 확인해 주세요.")
            return False
        self.pending_plan = plan
        self.phase = "deleting"
        self._set_notice("선택한 자료를 삭제하고 있습니다. 완료 전에는 다시 요청할 수 없습니다.")
        self._render()
        self.busy_changed.emit()
        gateway = self.gateway
        self.request.start(BackgroundRead(lambda: gateway.delete(plan)))
        return True

    def _completed(self, result, error):
        if self.phase == "loading":
            self.phase = "idle"
            if error is not None or not isinstance(result, DeletionCatalog):
                self.catalog = None
                self._set_notice("저장된 자료 목록을 읽지 못했습니다. 다시 조회해 주세요.")
                self._render()
            else:
                self.set_catalog(result)
        elif self.phase == "deleting":
            plan, self.pending_plan = self.pending_plan, None
            valid = error is None and isinstance(result, DeletionResult)
            if (valid and result.status == "success" and set(result.deleted_keys) == set(plan.keys)
                    and len(result.deleted_keys) == len(plan.keys) and result.revision
                    and result.revision != plan.revision):
                self.phase = "idle"
                self.catalog = DeletionCatalog(result.revision, tuple(b for b in self.catalog.batches if b.key not in plan.keys), self.catalog.source_label)
                self.selected.clear()
                self._set_notice("삭제 완료 · 기준 날짜와 조회 결과를 다시 확인해 주세요.")
                self.deleted.emit(result)
            elif valid and result.status == "failed" and not result.deleted_keys:
                self.phase = "idle"
                self._set_notice("삭제하지 못했습니다. 기존 자료와 선택은 유지됩니다. " + result.message)
            else:
                self.phase = "unknown"
                self._set_notice("삭제 결과를 확인할 수 없습니다. 중복 삭제를 요청하지 말고 저장 기록을 확인해 주세요.")
            self._render()
        self.busy_changed.emit()

    def closeEvent(self, event):
        if self.phase == "deleting":
            event.ignore()
            return
        self.request.cancel()
        super().closeEvent(event)
