"""설비에 실제 적용된 위험 기준과 저장된 기준 버전을 읽기 전용 표로 표시한다."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QSizePolicy, QVBoxLayout

from setron.features.risk.schemas import RiskStatus, RuleType
from setron.ui.components import label
from setron.ui.theme import INK, LINE, SECONDARY, SOFT


HEADERS = ("위험 기준명", "기준 버전", "메모(설명)", "적용 근거")
_BASIS = {
    RuleType.DIRECT_VERSION: "버전 일치",
    RuleType.VERSION_ABOVE: "버전 초과",
    RuleType.VERSION_BELOW: "버전 미만",
    RuleType.SAFETY_REQUIRED: "필수 Item 미적용",
    RuleType.DEFECT_ITEM: "버전 범위 내 결함 Item 포함",
}


def _rule_name(rule):
    return rule.display_name or rule.risk_reason or f"위험 기준 {rule.risk_rule_id}"


def _reference(rule, versions):
    def raw(release_id):
        return versions.get((rule.family_id, release_id)) or "미확인"

    if rule.rule_type is RuleType.SAFETY_REQUIRED:
        return "별도 기준 버전 없음"
    if rule.rule_type is RuleType.DEFECT_ITEM:
        start = f"시작 {raw(rule.start_release_version_id)} (포함)"
        end = (f"해제 {raw(rule.end_release_version_id)} (제외)"
               if rule.end_release_version_id is not None else "해제 버전 없음")
        return f"{start}\n{end}"
    return raw(rule.release_version_id)


def _cell(text, *, header=False, last=False):
    result = label(text, 13, medium=header, color=INK, wrap=True)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    result.setAlignment(Qt.AlignmentFlag.AlignCenter)
    result.setMinimumWidth(0)
    result.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    edge = "" if last else f"border-bottom:1px solid {LINE};"
    result.setStyleSheet(f"background:{SOFT if header else 'transparent'};color:{INK};border:0;"
                        f"{edge}padding:9px 6px;")
    return result


class RiskCriteriaTable(QFrame):
    """RISK만 기준별 한 행으로 모으고 미확인 근거는 표 아래에 별도로 보존한다.

    ``reference_versions``는 (계열 ID, 릴리즈 ID) → 원문 버전 매핑이다.
    관측 버전이나 목표 N을 기준 버전 대신 표시하지 않는다.
    """

    def __init__(self, findings, reference_versions=None):
        super().__init__()
        self.setObjectName("equipmentRiskCriteriaTable")
        self.setAccessibleName("설비에 적용된 위험 기준")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        versions = reference_versions or {}
        matched, unknown = {}, {}
        for finding in findings:
            rule = finding.rule
            if finding.status is RiskStatus.RISK:
                matched.setdefault(rule.risk_rule_id, rule)
            elif finding.status is RiskStatus.UNKNOWN:
                messages = unknown.setdefault(rule.risk_rule_id, (_rule_name(rule), []))[1]
                if finding.message not in messages:
                    messages.append(finding.message)
        self.rows = tuple((_rule_name(rule), _reference(rule, versions),
                           rule.memo if (rule.memo or "").strip() else "—", _BASIS[rule.rule_type])
                          for rule in matched.values())
        self.unknown_rows = tuple((name, " / ".join(messages)) for name, messages in unknown.values())

        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        self.table = QFrame()
        self.table.setObjectName("riskCriteriaGrid")
        self.table.setStyleSheet(f"QFrame#riskCriteriaGrid {{background:white;border:1px solid {LINE};border-radius:8px;}}")
        self.table.setMinimumWidth(0)
        grid = QGridLayout(self.table)
        grid.setContentsMargins(1, 1, 1, 1)
        grid.setSpacing(0)
        for column, stretch in enumerate((25, 23, 30, 22)):
            grid.setColumnMinimumWidth(column, 0)
            grid.setColumnStretch(column, stretch)
        for column, title in enumerate(HEADERS):
            grid.addWidget(_cell(title, header=True, last=not self.rows), 0, column)
        for row, values in enumerate(self.rows, 1):
            for column, value in enumerate(values):
                grid.addWidget(_cell(value, last=row == len(self.rows)), row, column)
        body.addWidget(self.table)
        self.table.setVisible(bool(self.rows))

        notice = ("미확인 · 판정 자료 확인 필요\n" + "\n".join(
            f"{name} · {message}" for name, message in self.unknown_rows)) if self.unknown_rows else ""
        self.unknown_notice = label(notice, 12, color=SECONDARY, wrap=True)
        self.unknown_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.unknown_notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.unknown_notice.setMinimumWidth(0)
        self.unknown_notice.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body.addWidget(self.unknown_notice)
        self.unknown_notice.setVisible(bool(self.unknown_rows))
