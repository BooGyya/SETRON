"""위험 후보의 판정 이유를 '적용 위험 / 미적용 위험'으로 나눠 보여 주는 공통 표시 부품.

판정은 RiskAssessor 결과(RiskFinding)를 그대로 읽기만 한다. 근거가 없으면 만들지 않고 '미연결'로 둔다.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from setron.features.risk.schemas import (
    RISK_KIND_DESCRIPTIONS, RISK_KIND_LABELS, RiskKind, RiskStatus, risk_kinds,
)
from setron.ui.components import StatusBadge, label
from setron.ui.theme import INK, LINE, SECONDARY, TONES

UNKNOWN_GROUP = "미확인"


def _wrapped(text, size, **options):
    """긴 기준명·문구가 패널 최소 폭을 늘려 가로 스크롤을 만들지 않게 한다(equipment.ignore_width와 같은 처리)."""
    result = label(text, size, wrap=True, **options)
    result.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    result.setMinimumWidth(0)
    return result


def kind_badge(kind: RiskKind) -> StatusBadge:
    """적용 위험은 채운 위험색, 미적용 위험은 흰 바탕 + 위험색 테두리로 모양을 달리한다(같은 위험 토큰 사용)."""
    badge = StatusBadge(RISK_KIND_LABELS[kind], "danger")
    badge.setObjectName("riskKindBadge")
    badge.setProperty("riskKind", kind.value)
    badge.setToolTip(RISK_KIND_DESCRIPTIONS[kind])
    if kind is RiskKind.NOT_APPLIED:
        _, ink, border = TONES["danger"]
        badge.setStyleSheet(f"color:{ink};background:white;border:1px solid {border};border-radius:4px;padding:3px 8px;")
    return badge


def kinds_text(kinds) -> str:
    return " · ".join(RISK_KIND_LABELS[k] for k in kinds)


def finding_detail(finding) -> str:
    """'PM · 버전 · 판정 문구' 한 줄. 버전·PM은 관측·릴리즈에 있는 값만 쓴다."""
    parts = []
    if finding.pm is not None:
        parts.append(finding.pm.pm_name or "공통 SW")
    if finding.release is not None:
        parts.append(f"버전 {finding.release.version_raw}")
    parts.append(finding.message)
    return " · ".join(parts)


def rule_name(rule) -> str:
    return rule.display_name or rule.risk_reason or f"위험 기준 {rule.risk_rule_id}"


def reason_groups(findings):
    """(분류 또는 '미확인', [(기준명, 상세 줄), ...]) 목록. 같은 기준·같은 줄은 한 번만 둔다."""
    groups = {}
    for finding in findings:
        key = finding.kind if finding.status is RiskStatus.RISK else UNKNOWN_GROUP
        lines = groups.setdefault(key, [])
        line = (rule_name(finding.rule), finding_detail(finding))
        if line not in lines:
            lines.append(line)
    order = [*RiskKind, UNKNOWN_GROUP]
    return [(key, groups[key]) for key in order if key in groups]


class RiskReasonCard(QFrame):
    """설비 상세 패널의 '위험 판정 이유' 카드. 위험·미확인 설비에만 쓴다."""

    def __init__(self, status: RiskStatus, findings):
        super().__init__()
        self.setObjectName("drawerRiskReasons")
        self.setStyleSheet(f"QFrame#drawerRiskReasons {{background:white;border:1px solid {LINE};border-radius:12px;}}")
        self.kinds = risk_kinds(findings)
        self.kind_badges = {}
        body = QVBoxLayout(self)
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(10)
        body.addWidget(label("위험 판정 이유", 15, medium=True))
        groups = reason_groups(findings)
        if not groups:
            text = ("세부 근거 미연결 · 위험 후보로 판정한 규칙 근거를 읽지 못했습니다." if status is RiskStatus.RISK
                    else "판정 근거 미연결 · 위험 미확인")
            self.empty = _wrapped(text, 13, color=SECONDARY)
            body.addWidget(self.empty)
            return
        self.empty = None
        for key, lines in groups:
            heading = QHBoxLayout()
            heading.setSpacing(8)
            if key == UNKNOWN_GROUP:
                badge = StatusBadge(UNKNOWN_GROUP, "warning")
                caption = "위험 여부를 확인할 수 없는 기준"
            else:
                badge = kind_badge(key)
                caption = RISK_KIND_DESCRIPTIONS[key]
            self.kind_badges[key] = badge
            heading.addWidget(badge)
            heading.addWidget(_wrapped(caption, 13, color=SECONDARY), 1)
            body.addLayout(heading)
            for name, detail in lines:
                row = QVBoxLayout()
                row.setContentsMargins(4, 0, 0, 0)
                row.setSpacing(2)
                title = _wrapped(name, 14, medium=True, color=INK)
                title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                row.addWidget(title)
                row.addWidget(_wrapped(detail, 13, color=SECONDARY))
                body.addLayout(row)

    def text(self) -> str:
        """테스트·접근성용 전체 문구."""
        return "\n".join(w.text() for w in self.findChildren(QLabel))
