"""VER5 2-1: 히트맵을 떠나지 않는 읽기 전용 설비 상세 패널.

판정은 EquipmentPage가 사용하는 같은 관측·비교 결과를 읽기 전용으로 표시한다.
"""

from collections import Counter
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget,
)

from setron.features.risk.schemas import RiskStatus
from setron.ui.components import StatusBadge, label
from setron.ui.drawer import SideDrawer
from setron.ui.equipment import ignore_width
from setron.ui.heatmap_data import GoalState
from setron.ui.risk_reasons import RiskReasonCard
from setron.ui.theme import CANVAS, LINE, SECONDARY


@dataclass(frozen=True)
class ObservedSoftware:
    model: str
    pms: tuple[str, ...]
    current: str
    target: str


def observed_software(detail, rows):
    """같은 모델·계열에 현재/목표 버전이 같은 PM을 묶는다."""
    identities = [(p.pm.group_id, f.family_id, p.pm.pm_name)
                  for f in detail.status.families for p in f.pms] if detail.status else [
                      (p.group_id, p.family_id, p.pm_name) for p in detail.record.pms]
    grouped = {}
    for row, (model_id, family_id, pm_name) in zip(rows, identities, strict=True):
        model, current, target = row[1:4]
        key = model_id, family_id, model, current, "해당 없음" if detail.record.is_hpa else target
        names = grouped.setdefault(key, [])
        if pm_name:
            names.append(pm_name)
    return tuple(ObservedSoftware(key[2], tuple(pms), *key[3:]) for key, pms in grouped.items())


GOAL_TONES = {
    GoalState.MET: "success",
    GoalState.BELOW: "danger",
    GoalState.UNKNOWN: "warning",
    GoalState.EXCLUDED: "neutral",
}


class SoftwareCard(QFrame):
    def __init__(self, observation, title):
        super().__init__()
        self.observation = observation
        self.setObjectName("drawerSoftware")
        self.setStyleSheet(f"QFrame#drawerSoftware {{background:white;border:1px solid {LINE};border-radius:12px;}}")
        body = QVBoxLayout(self)
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(8)
        if title:
            heading = label(title, 14, medium=True, wrap=True)
            ignore_width(heading)
            body.addWidget(heading)
        versions = QGridLayout()
        versions.setHorizontalSpacing(14)
        versions.setVerticalSpacing(6)
        for row, (name, version) in enumerate((("현재 버전", observation.current), ("목표 버전", observation.target))):
            versions.addWidget(label(name, 14, color=SECONDARY), row, 0, Qt.AlignmentFlag.AlignTop)
            value = label(version, 16, medium=True, wrap=True)
            ignore_width(value)
            versions.addWidget(value, row, 1)
        versions.setColumnStretch(1, 1)
        body.addLayout(versions)


class EquipmentDrawer(SideDrawer):
    """중앙 캔버스 위에 얹는 560px 패널. 화면 라우트/필터/스크롤은 바꾸지 않는다."""

    closed = Signal()

    def __init__(self, parent, *, object_name="equipmentDrawer", accessible_name="설비 상세 패널"):
        super().__init__(parent, object_name=object_name, accessible_name=accessible_name)
        self.request, self.detail, self.return_focus = None, None, None
        self.software_cards = []
        self.risk_reasons = None
        self.title = label("설비 정보", 24, medium=True, wrap=True)
        ignore_width(self.title)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addLayout(self.header)
        heading.addWidget(self.title)
        self.panel_layout.addLayout(heading)
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

    def open_detail(self, request, detail, rows, *, return_focus=None):
        self._clear_body()
        self.request, self.detail, self.return_focus = request, detail, return_focus
        self.title.setText("설비 정보")
        self._build_info(detail)
        whole = detail.whole
        # 위험 후보는 어떤 기준 때문인지(적용 위험 / 미적용 위험)를 바로 아래에 둔다. 근거가 없으면 '미연결'로 둔다.
        # 미확인은 판정기가 남긴 근거가 있을 때만 보인다(기준 미등록 등으로 근거가 없으면 상단 배지로 충분).
        if not detail.record.is_hpa and (whole.risk is RiskStatus.RISK
                                         or (whole.risk is RiskStatus.UNKNOWN and whole.findings)):
            self.risk_reasons = RiskReasonCard(whole.risk, whole.findings)
            self.body.addWidget(self.risk_reasons)
        observations = observed_software(detail, rows)
        model_counts = Counter(observation.model for observation in observations)
        for observation in observations:
            title = observation.model if len(observations) > 1 else ""
            if model_counts[observation.model] > 1 and observation.pms:
                title = " · ".join((title, ", ".join(observation.pms)))
            card = SoftwareCard(observation, title)
            self.software_cards.append(card)
            self.body.addWidget(card)
        if not observations and not detail.record.is_hpa:
            empty = label("SW 미등록 · 목표 비교 불가", 14, wrap=True)
            ignore_width(empty)
            self.body.addWidget(empty)
        self._build_risk(detail)
        self.show_panel()
        self.scroll.verticalScrollBar().setValue(0)

    def _build_risk(self, detail):
        whole = detail.whole
        excluded = whole.goal is GoalState.EXCLUDED
        tone = "neutral" if excluded else {
            RiskStatus.RISK: "danger", RiskStatus.UNKNOWN: "warning",
        }.get(whole.risk, "neutral")
        self.risk_card = QFrame()
        self.risk_card.setObjectName("drawerRisk")
        self.risk_card.setStyleSheet(f"QFrame#drawerRisk {{background:white;border:1px solid {LINE};border-radius:12px;}}")
        body = QVBoxLayout(self.risk_card)
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(8)
        self.risk_title = label("위험버전", 16, medium=True, wrap=True)
        self.risk_status_badge = StatusBadge(whole.risk_label, tone)
        reason = ("HPA는 위험 분석에서 제외됩니다." if excluded else whole.risk_detail or {
            RiskStatus.RISK: "위험 후보로 판정되었습니다. 상세 근거를 확인해 주세요.",
            RiskStatus.NO_RISK: "현재 등록된 규칙에서는 위험 후보로 판정되지 않았습니다.",
            RiskStatus.UNKNOWN: "위험 여부를 판정할 자료가 부족합니다.",
        }[whole.risk])
        self.risk_reason = label(reason, 14, color=SECONDARY, wrap=True)
        ignore_width(self.risk_title, self.risk_reason)
        body.addWidget(self.risk_title)
        body.addWidget(self.risk_status_badge, alignment=Qt.AlignmentFlag.AlignLeft)
        body.addWidget(self.risk_reason)
        self.body.addWidget(self.risk_card)

    def _build_info(self, detail):
        whole, record = detail.whole, detail.record
        self.info_card = QFrame()
        self.info_card.setObjectName("drawerInfo")
        self.info_card.setStyleSheet(f"QFrame#drawerInfo {{background:white;border:1px solid {LINE};border-radius:12px;}}")
        card = QVBoxLayout(self.info_card)
        card.setContentsMargins(14, 12, 14, 12)
        card.setSpacing(12)
        self.risk_badge = self.goal_badge = None
        if not record.is_hpa:
            badges = QHBoxLayout()
            badges.setSpacing(8)
            risk_tone = {RiskStatus.RISK: "danger", RiskStatus.UNKNOWN: "warning"}.get(whole.risk, "neutral")
            self.risk_badge = StatusBadge(whole.risk_label, risk_tone)
            self.goal_badge = StatusBadge(whole.goal.value, GOAL_TONES[whole.goal])
            badges.addWidget(self.risk_badge)
            badges.addWidget(self.goal_badge)
            badges.addStretch()
            card.addLayout(badges)
        fields = QGridLayout()
        fields.setHorizontalSpacing(14)
        fields.setVerticalSpacing(8)
        self.info_values = {}
        for row, (name, value) in enumerate((("Site", whole.site_label), ("Line", whole.line_label or "미지정"),
                                             ("모델", record.model_name or "미확인"), ("설비명", record.equipment_code),
                                             ("기준일", str(record.base_date)))):
            fields.addWidget(label(name, 14, color=SECONDARY), row, 0, Qt.AlignmentFlag.AlignTop)
            text = label(value, 15, medium=True, wrap=True)
            ignore_width(text)
            fields.addWidget(text, row, 1)
            self.info_values[name] = text
        fields.setColumnStretch(1, 1)
        card.addLayout(fields)
        self.body.addWidget(self.info_card)

    def _clear_body(self):
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
        self.risk_reasons = None

    def clear_detail(self):
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
