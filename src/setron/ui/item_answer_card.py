"""Figma 08-1의 답변 카드. 모든 숫자는 ItemAnswer가 검증된 관측으로 계산한다."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from setron.features.item.schemas import Application
from setron.infrastructure.ai_client import ResponseSource
from setron.ui.components import StatusBadge, button, label
from setron.ui.equipment import FlowGrid, icon_label, ignore_width
from setron.ui.query_panel import _WrapRow
from setron.ui.theme import PRIMARY, SECONDARY


class _AnswerActions(_WrapRow):
    """넓을 때 우측 한 줄, 좁을 때 각 줄의 끝에 맞춘다. 버튼 순서는 유지한다."""

    def _arrange(self, columns):
        if self.columns == columns:
            return
        for index in range(max(self.columns, columns) + 2):
            self.grid.setColumnStretch(index, 0)
        for widget in self.widgets:
            self.grid.removeWidget(widget)
        for start in range(0, len(self.widgets), columns):
            group = self.widgets[start:start + columns]
            for offset, widget in enumerate(group):
                self.grid.addWidget(widget, start // columns, columns - len(group) + offset + 1,
                                    Qt.AlignmentFlag.AlignRight)
        self.grid.setColumnStretch(0, 1)
        self.columns = columns
        self.updateGeometry()


class ItemAnswerCard(QFrame):
    navigate_requested = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.answer = None
        self.setObjectName("itemAnswer")
        self.setStyleSheet("QFrame#itemAnswer {background:#f1f4fd;border:1px solid #bfc9f5;border-radius:12px;}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(icon_label("9d5d2.svg"))
        header.addWidget(label("AI 답변", 15, medium=True, color=PRIMARY))
        self.source = label("", 12, wrap=True, color=SECONDARY)
        ignore_width(self.source)
        self.source.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.source.setMinimumHeight(32)
        header.addWidget(self.source, 1)
        self.body.addLayout(header)
        self.message = label("", 13, wrap=True)
        ignore_width(self.message)
        self.body.addWidget(self.message)
        self.columns = FlowGrid(340, max_columns=2, spacing=28)
        self.body.addWidget(self.columns)
        self.boundary = label("동일 계열의 포함 버전 이상 누적 적용 · 여러 Item은 하나 이상 적용(OR) · 현장 기능 활성화 여부 별도 확인", 12,
                              color=SECONDARY, wrap=True)
        ignore_width(self.boundary)
        self.body.addWidget(self.boundary)
        self.navigation_note = label("다른 화면 이동은 버튼을 누를 때만 조회 조건을 적용합니다.", 12, color=SECONDARY, wrap=True)
        ignore_width(self.navigation_note)
        self.body.addWidget(self.navigation_note)
        self.actions = {}
        for route, text in (("ai", "AI 분석에서 이어서 묻기"), ("heatmap", "히트맵에서 보기"), ("dashboard", "대시보드에서 보기")):
            control = button(text, primary=route == "dashboard")
            control.clicked.connect(lambda checked=False, r=route: self.navigate_requested.emit(r, self.answer))
            self.actions[route] = control
        self.action_row = _AnswerActions(list(self.actions.values()), 180)
        self.body.addWidget(self.action_row)
        self.hide()

    def show_message(self, text, *, loading=False):
        self.answer = None
        self.message.setText(text)
        self.source.setText("조회 중" if loading else "검색 결과")
        self.columns.hide()
        self.boundary.hide()
        self.navigation_note.hide()
        for control in self.actions.values():
            control.setEnabled(False)
        self.show()

    def show_answer(self, answer):
        self.answer = answer
        data = answer.data
        source = "실제 AI 응답" if answer.source is ResponseSource.REAL else "AI 미연결 · 키워드 검색" if answer.source is ResponseSource.RULE else "테스트 응답 · 실제 AI 아님"
        self.source.setText(f"{source} · 관측일 {data.base_date or '미연결'}")
        self.message.setText(answer.explanation or ("관련 Item을 찾았습니다." if answer.item_ids else "관련 Item을 찾지 못했습니다."))
        self.boundary.setVisible(bool(answer.item_ids))
        self.navigation_note.setVisible(bool(answer.item_ids))
        versions = QWidget()
        left = QVBoxLayout(versions)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left.addWidget(label("포함 버전 · 원본 근거", 12, medium=True, color=SECONDARY))
        entries = {e.item.item_id: e for e in data.versions.items}
        ordered = answer.versions
        first = next((r for r in ordered if r.version_order is not None), None)
        for release in ordered[:10]:
            row = QHBoxLayout()
            unique = first is release and release.version_order not in data.versions.families()[answer.family_id].sorted.duplicated_orders
            row.addWidget(StatusBadge("최초 확인 포함" if unique else "포함 확인", "info" if unique else "neutral"))
            item_ids = {i for i in answer.item_ids if data.catalog().version_state(i, release).state.value == "APPLIED"}
            numbers = " · ".join(f"No {entries[i].item.item_no if entries[i].item.item_no is not None else '미확인'} / Item {i}" for i in sorted(item_ids))
            text = label(f"{release.version_raw} ({release.released_at or '출시일 미확인'})\n{numbers}", 13, medium=True, wrap=True)
            ignore_width(text)
            row.addWidget(text, 1)
            left.addLayout(row)
        if len(ordered) > 10:
            left.addWidget(label(f"그 외 누적 적용 버전 {len(ordered) - 10}개 · Item 상세에서 전체 확인", 12, color=SECONDARY, wrap=True))
        if not ordered:
            left.addWidget(label("포함으로 확인된 릴리즈가 없습니다.", 13, wrap=True))
        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(label("적용 현황 · 물리 설비 중복 제거", 12, medium=True, color=SECONDARY))
        total = answer.counts()
        ratio = f"{total.rate:.0%}" if total.rate is not None else "계산 불가"
        self.total = label(f"{total.applied} / {total.determined}대   {ratio}", 24, medium=True)
        right.addWidget(self.total)
        sites = sorted({r.site for r, _ in answer.applications})
        for site in sites:
            counts = answer.counts(site)
            row = QHBoxLayout()
            row.addWidget(label(site, 12))
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(round(100 * counts.rate) if counts.rate is not None else 0)
            progress.setTextVisible(False)
            progress.setFixedHeight(8)
            progress.setStyleSheet("QProgressBar {background:#dee0e5;border:0;border-radius:4px;}"
                                  "QProgressBar::chunk {background:#1f8c59;border-radius:4px;}")
            row.addWidget(progress, 1)
            row.addWidget(label(f"{counts.applied}/{counts.determined}", 12))
            right.addLayout(row)
        missing = [record for record, results in answer.applications if answer.application(results) is Application.NOT_APPLIED]
        text = label("미적용 " + str(len(missing)) + "대 · " + (" · ".join(
            f"{r.equipment_code} ({r.site} {r.line or 'Line 미지정'})" for r in missing) or "없음"), 12, color="#c23345", wrap=True)
        ignore_width(text)
        right.addWidget(text)
        right.addWidget(label(f"미확인 {total.unknown}대 · 대상 외 {total.not_target}대 · HPA 제외 {sum(r.is_hpa for r in data.records)}대", 12,
                              color=SECONDARY, wrap=True))
        if not data.observations_connected:
            right.addWidget(label("설비 관측 자료 미연결 · 0대/안전 확정 아님", 12, color="#b88000", wrap=True))
        self.columns.set_widgets((versions, right_widget))
        self.columns.setVisible(bool(answer.item_ids))
        for route, control in self.actions.items():
            control.setEnabled(bool(answer.item_ids) and (route == "ai" or bool(ordered) and data.observations_connected))
        self.show()
