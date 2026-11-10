"""VER5 2-1: 히트맵을 떠나지 않는 읽기 전용 설비 상세 패널.

판정은 EquipmentPage가 사용하는 같은 관측·비교 결과를 표시한다. 이 뷰는 버전 순서나
위험 규칙을 추론하지 않으며, 편집은 기존 관리 화면의 검토/저장 흐름에 위임한다.
"""

from dataclasses import dataclass

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import RiskStatus
from setron.features.version.schemas import CompareStatus
from setron.ui.components import DisclosureArrow, StatusBadge, button, label
from setron.ui.drawer import SideDrawer
from setron.ui.equipment import icon_label, ignore_width
from setron.ui.heatmap_data import GoalState
from setron.ui.theme import CANVAS, SECONDARY


@dataclass(frozen=True)
class ObservedSoftware:
    model: str
    pms: tuple[str, ...]
    current: str
    target: str
    judgement: str
    reason: str
    state: str


def observed_software(detail, rows):
    """같은 모델/계열/버전/판정의 PM만 묶는다. 설비 전체의 관측을 버리지 않는다."""
    identities = [(p.pm.group_id, f.family_id, p.comparison.status)
                  for f in detail.status.families for p in f.pms] if detail.status else [
                      (p.group_id, p.family_id, None) for p in detail.record.pms]
    grouped = {}
    for row, (model_id, family_id, status) in zip(rows, identities, strict=True):
        state = ("excluded" if detail.record.is_hpa else
                 "met" if status is CompareStatus.AT_OR_ABOVE else
                 "below" if status is CompareStatus.BELOW else "unknown")
        key = (model_id, family_id, *row[1:], state)
        grouped.setdefault(key, []).append(row[0])
    return tuple(ObservedSoftware(key[2], tuple(pms), *key[3:]) for key, pms in grouped.items())


# Figma 2-1 상태별 카드. 목표 미달과 위험 후보는 별개이며, 아래 색은 N 비교에만 쓴다.
PALETTES = {
    "met": ("#add6bd", "#edf6f2", "#e0efe8", "#1f8c59", "#e9f4ee", "b5c69.svg", "08019.svg"),
    "below": ("#e3babf", "#faeff0", "#f6e2e5", "#c23345", "#f9ebec", "1512d.svg", "96d37.svg"),
    "unknown": ("#e3c787", "#f9f5eb", "#f5eddb", "#b88000", "#fbf4e2", "ed395.svg", "98d75.svg"),
    "excluded": ("#d1d6de", "#f2f3f4", "#e9eaec", "#5e6b7a", "#eff0f2", "6110f.svg", "02a44.svg"),
}


def _column(asset, title, value, *, size=12):
    result = QWidget()
    body = QVBoxLayout(result)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(5)
    head = QHBoxLayout()
    head.setSpacing(6)
    head.addWidget(icon_label(asset))
    head.addWidget(label(title, 11, color=SECONDARY), 1)
    body.addLayout(head)
    text = label(str(value), size, medium=True, wrap=True)
    ignore_width(text)
    body.addWidget(text)
    return result


class SoftwareCard(QFrame):
    def __init__(self, observation, source):
        super().__init__()
        self.observation = observation
        border, header, pill, ink, soft, status_icon, _ = PALETTES[observation.state]
        self.setObjectName("drawerSoftware")
        self.setStyleSheet(f"QFrame#drawerSoftware {{background:white;border:1px solid {border};border-radius:12px;}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(1, 1, 1, 1)
        body.setSpacing(0)
        self.toggle = QPushButton()
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setAccessibleName(observation.model + " SW 카드 접기")
        self.toggle.setStyleSheet(f"QPushButton {{background:{header};border:0;border-radius:10px;padding:0;min-height:54px;}}"
                                 "QPushButton:focus {border:2px solid #3c5ce9;}")
        head = QHBoxLayout(self.toggle)
        head.setContentsMargins(14, 12, 14, 12)
        head.setSpacing(8)
        title = label(observation.model, 14, medium=True, wrap=True)
        ignore_width(title)
        head.addWidget(title, 1)
        status = QWidget()
        status.setStyleSheet(f"background:{pill};border-radius:6px;")
        status_row = QHBoxLayout(status)
        status_row.setContentsMargins(6, 4, 6, 4)
        status_row.setSpacing(4)
        status_row.addWidget(icon_label(status_icon))
        status_row.addWidget(label(observation.judgement, 12, medium=True, color=ink))
        head.addWidget(status)
        self.chevron = DisclosureArrow(expanded=True)
        head.addWidget(self.chevron)
        for child in self.toggle.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        body.addWidget(self.toggle)
        self.contents = QWidget()
        details = QVBoxLayout(self.contents)
        details.setContentsMargins(14, 12, 14, 14)
        details.setSpacing(10)
        versions = QFrame()
        versions.setObjectName("drawerVersions")
        versions.setStyleSheet("QFrame#drawerVersions {background:#f4f5f8;border-radius:6px;}")
        fields = QHBoxLayout(versions)
        fields.setContentsMargins(12, 10, 12, 10)
        fields.setSpacing(12)
        fields.addWidget(_column("50857.svg", "현재 버전", observation.current, size=20), 1)
        connector = ("·" if observation.state == "excluded" else "?" if observation.state == "unknown"
                     else "=" if observation.current == observation.target else "→")
        fields.addWidget(label(connector, 20, medium=True))
        fields.addWidget(_column("2c52c.svg", "목표 기준 N",
                                 "해당 없음" if observation.state == "excluded" else observation.target, size=20), 1)
        details.addWidget(versions)
        reason = QFrame()
        reason.setObjectName("drawerReason")
        reason.setStyleSheet(f"QFrame#drawerReason {{background:{soft};border-radius:6px;}}")
        reason_body = QVBoxLayout(reason)
        reason_body.setContentsMargins(12, 10, 12, 10)
        reason_body.setSpacing(4)
        reason_body.addWidget(label(observation.judgement + " 근거", 13, medium=True, color=ink))
        reason_text = label(observation.reason, 12, wrap=True)
        ignore_width(reason_text)
        reason_body.addWidget(reason_text)
        details.addWidget(reason)
        footer = QHBoxLayout()
        footer.setSpacing(12)
        footer.addWidget(_column("ba1bd.svg", "설치 PM · 관측 위치", " · ".join(observation.pms)), 1)
        footer.addWidget(_column("58593.svg", "관측 출처", source), 1)
        details.addLayout(footer)
        body.addWidget(self.contents)
        self.toggle.toggled.connect(self.contents.setVisible)
        self.toggle.toggled.connect(self.chevron.set_expanded)
        self.toggle.toggled.connect(lambda expanded: self.toggle.setAccessibleName(
            observation.model + " SW 카드 " + ("접기" if expanded else "펼치기")))


class EquipmentDrawer(SideDrawer):
    """중앙 캔버스 위에 얹는 560px 패널. 화면 라우트/필터/스크롤은 바꾸지 않는다."""

    closed = Signal()
    edit_requested = Signal()
    record_requested = Signal(str, object)

    def __init__(self, parent, *, allow_expand=False, object_name="equipmentDrawer", accessible_name="설비 상세 패널"):
        super().__init__(parent, object_name=object_name, accessible_name=accessible_name,
                         allow_expand=allow_expand)
        self.request, self.detail, self.return_focus = None, None, None
        self.edit_available = False
        self.software_cards = []
        self.edit_menu = None
        self.title = label("설비 상세", 24, medium=True, wrap=True)
        ignore_width(self.title)
        self.header.addWidget(self.title, 1)
        self.panel_layout.addLayout(self.header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.panel_layout.addWidget(self.scroll, 1)
        self.body_widget = QWidget()
        self.body_widget.setObjectName("equipmentDrawerBody")
        self.body_widget.setStyleSheet(f"QWidget#equipmentDrawerBody {{background:{CANVAS};}}")
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(14)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.body_widget)

    def open_detail(self, request, detail, rows, evidence, source, *, return_focus=None, edit_available=False, below_evidence=()):
        self.closed.emit()  # 같은 패널 재선택 시 이전 비동기 편집 조회도 취소한다.
        self._clear_body()
        self.request, self.detail, self.return_focus = request, detail, return_focus
        self.edit_available = edit_available and bool(rows)
        whole, record = detail.whole, detail.record
        self.title.setText(f"{record.equipment_code} · {record.model_name or '설비 상세'}")
        badges = QHBoxLayout()
        badges.setSpacing(8)
        self.risk_badge = StatusBadge(whole.risk_label, "danger" if whole.risk is RiskStatus.RISK and not record.is_hpa
                                     else "warning" if whole.risk is RiskStatus.UNKNOWN and not record.is_hpa else "neutral")
        badges.addWidget(self.risk_badge)
        badges.addStretch()
        badges.addWidget(label(str(record.base_date), 12, medium=True))
        self.body.addLayout(badges)
        location = label(whole.location + " · 물리 설비 1대", 12, wrap=True)
        ignore_width(location)
        self.body.addWidget(location)
        observations = observed_software(detail, rows)
        state = {GoalState.MET: "met", GoalState.BELOW: "below", GoalState.EXCLUDED: "excluded"}.get(whole.goal, "unknown")
        border, _, _, ink, soft, _, summary_icon = PALETTES[state]
        summary = QFrame()
        summary.setObjectName("drawerSummary")
        summary.setStyleSheet(f"QFrame#drawerSummary {{background:{soft};border:1px solid {border};border-radius:12px;}}")
        box = QVBoxLayout(summary)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(label("판정 요약", 15, medium=True))
        heading.addStretch()
        heading.addWidget(label(f"관측 SW {len(observations)}건", 12))
        box.addLayout(heading)
        verdict = QHBoxLayout()
        verdict.addWidget(icon_label(summary_icon))
        self.summary_title = label(whole.goal.value, 24, medium=True, color=ink)
        verdict.addWidget(self.summary_title, 1)
        box.addLayout(verdict)
        note = ("SW 판정 대상 아님 · 안전 확정 아님" if record.is_hpa else
                f"설비 전체 등록 SW 기준 · 선택 SW {detail.selected.goal.value}\n목표 충족과 위험 여부는 별도 판정")
        if below_evidence:
            note = "\n".join(("미달 판정 근거", *below_evidence))
        summary_note = label(note, 12, wrap=True, color=SECONDARY)
        ignore_width(summary_note)
        box.addWidget(summary_note)
        self.body.addWidget(summary)
        self.body.addWidget(label(f"관측된 SW ({len(observations)}건)", 14, medium=True))
        self.software_cards = [SoftwareCard(o, source) for o in observations]
        for card in self.software_cards:
            self.body.addWidget(card)
        if not observations:
            empty = label("HPA · SW 관측 없음 · 분석 제외" if record.is_hpa else "SW 미등록 · 목표 비교 불가", 14, wrap=True)
            ignore_width(empty)
            self.body.addWidget(empty)
        self._build_evidence(evidence)
        self.edit_button = button("버전 정보 수정", primary=True)
        self.edit_button.setFixedHeight(40)
        self.edit_button.setEnabled(self.edit_available)
        self.edit_button.setToolTip("해당 관측의 SW를 선택해 관리 화면에서 검토·저장합니다." if self.edit_available else
                                    "DB 관리 연결과 저장된 SW 관측이 있어야 수정할 수 있습니다.")
        self.edit_button.clicked.connect(self.edit_requested)
        self.body.addWidget(self.edit_button)
        self.edit_notice = label("", 12, color=SECONDARY, wrap=True)
        ignore_width(self.edit_notice)
        self.edit_notice.hide()
        self.body.addWidget(self.edit_notice)
        self.show_panel()
        self.scroll.verticalScrollBar().setValue(0)

    def _build_evidence(self, evidence):
        frame = QFrame()
        frame.setObjectName("drawerEvidence")
        frame.setStyleSheet("QFrame#drawerEvidence {background:white;border:1px solid #dee0e5;border-radius:12px;}")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 10, 14, 14)
        box.setSpacing(8)
        self.evidence_toggle = button("")
        self.evidence_toggle.setCheckable(True)
        self.evidence_toggle.setAccessibleName("판정 · 원본 근거 펼치기")
        self.evidence_toggle.setStyleSheet("QPushButton {padding:0;border:0;border-radius:4px;background:transparent;text-align:left;}"
                                          "QPushButton:focus {border:2px solid #3c5ce9;}")
        heading = QHBoxLayout(self.evidence_toggle)
        heading.setContentsMargins(0, 2, 0, 2)
        heading.setSpacing(8)
        heading.addWidget(icon_label("02346.svg"))
        heading.addWidget(label("판정 · 원본 근거", 15, medium=True))
        heading.addStretch()
        self.evidence_chevron = DisclosureArrow()
        heading.addWidget(self.evidence_chevron)
        for child in self.evidence_toggle.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box.addWidget(self.evidence_toggle)
        self.evidence_content = QWidget()
        rows = QVBoxLayout(self.evidence_content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(8)
        self.evidence_values = tuple(evidence)
        for asset, title, value in evidence:
            row = QHBoxLayout()
            row.setSpacing(14)
            tag = QWidget()
            tag.setFixedWidth(190)
            tag.setStyleSheet("background:#f4f5f8;border-radius:6px;")
            tag_row = QHBoxLayout(tag)
            tag_row.setContentsMargins(10, 6, 10, 6)
            tag_row.setSpacing(8)
            tag_row.addWidget(icon_label(asset))
            name = label(title, 12, medium=True, wrap=True)
            ignore_width(name)
            tag_row.addWidget(name, 1)
            row.addWidget(tag)
            text = label(str(value), 12, wrap=True)
            ignore_width(text)
            row.addWidget(text, 1)
            rows.addLayout(row)
        box.addWidget(self.evidence_content)
        self.evidence_content.hide()
        self.evidence_toggle.toggled.connect(self.evidence_content.setVisible)
        self.evidence_toggle.toggled.connect(self.evidence_chevron.set_expanded)
        self.evidence_toggle.toggled.connect(lambda checked: self.evidence_toggle.setAccessibleName(
            "판정 · 원본 근거 " + ("접기" if checked else "펼치기")))
        self.body.addWidget(frame)

    def set_edit_loading(self, loading, message=""):
        self.edit_button.setEnabled(self.edit_available and not loading)
        self.edit_button.setText("수정할 SW 확인 중…" if loading else "버전 정보 수정")
        self.edit_notice.setText(message)
        self.edit_notice.setVisible(bool(message))

    def choose_edit_record(self, choices):
        """DB에서 확인한 실제 기본키만 전달한다. 화면 호기를 DB 행 ID로 추측하지 않는다."""
        self.set_edit_loading(False)
        if not choices:
            self.set_edit_loading(False, "이 관측의 수정 가능한 SW 기록이 없습니다. 다시 조회해 주세요.")
            return
        if len(choices) == 1:
            table, key, _ = choices[0]
            self.record_requested.emit(table, key)
            return
        if self.edit_menu is not None:
            self.edit_menu.close()
            self.edit_menu.deleteLater()
        self.edit_menu = QMenu(self)
        for table, key, title in choices:
            action = self.edit_menu.addAction(title)
            action.triggered.connect(lambda checked=False, t=table, k=key: self.record_requested.emit(t, k))
        self.edit_menu.popup(self.edit_button.mapToGlobal(QPoint(0, self.edit_button.height())))

    def _clear_body(self):
        if self.edit_menu is not None:
            self.edit_menu.close()
            self.edit_menu.deleteLater()
            self.edit_menu = None
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().hide()
                        child.widget().deleteLater()
        self.software_cards = []

    def clear_detail(self):
        if self.edit_menu is not None:
            self.edit_menu.close()
        self.request, self.detail = None, None

    def close_panel(self, *, restore_focus=True):
        if not self.is_open:
            if not restore_focus:
                self.hide_panel(animated=False)
            return
        self.hide_panel(animated=restore_focus)
        self.clear_detail()
        return_focus, self.return_focus = self.return_focus, None
        self.closed.emit()
        if restore_focus and return_focus is not None:
            try:
                if return_focus.isVisible() and return_focus.isEnabled():
                    return_focus.setFocus(Qt.FocusReason.OtherFocusReason)
            except RuntimeError:
                pass  # 새 조회로 타일 위젯이 이미 교체된 경우
