"""위험 후보의 판정 기준 분류(적용 위험 / 미적용 위험)와 대시보드 히트맵·사이드 패널 표시."""

from datetime import date
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.aggregation.schemas import EquipmentRecord, PmVersion
from setron.features.item.schemas import ItemState, TargetScope, VersionItem
from setron.features.item.service import ItemCatalog
from setron.features.risk.schemas import (
    MappingStatus, RiskFinding, RiskKind, RiskRule, RiskStatus, RuleType, FindingBasis, risk_kinds,
)
from setron.features.risk.service import RiskAssessor
from setron.features.version.schemas import ReleaseVersion
from setron.features.version.service import FamilyReleases
from setron.ui.app import create_window
from setron.ui.dashboard_data import DashboardDrilldown, DashboardSource, EquipmentLocation, GroupBinding
from setron.ui.query_state import GroupOption, QueryCatalog, SiteOption
from setron.ui.risk_reasons import RiskReasonCard
from setron.ui.theme import install_theme

DAY = date(2026, 10, 21)
SAFETY = RiskRule(1, RuleType.SAFETY_REQUIRED, 1001, "인터록 필수", item_id=7, mapping_status=MappingStatus.VERIFIED,
                  display_name="안전 인터록 필수 적용")
DIRECT = RiskRule(2, RuleType.DIRECT_VERSION, 1001, "v3 통신 오류", release_version_id=10013,
                  mapping_status=MappingStatus.VERIFIED)
DEFECT = RiskRule(3, RuleType.DEFECT_ITEM, 1001, "결함", item_id=7, start_release_version_id=10012,
                  mapping_status=MappingStatus.VERIFIED)


def test_rule_types_split_into_applied_and_not_applied_kinds():
    # 버전을 썼을 때 기준: 결함 Item·위험 버전을 '사용(적용)'해서 위험 / 필수 Item이 '미적용'이라 위험.
    release = ReleaseVersion(10011, 1001, "v1", 1)
    found = (RiskFinding(SAFETY, RiskStatus.RISK, FindingBasis.SAFETY_NOT_APPLIED, release=release),
             RiskFinding(DIRECT, RiskStatus.RISK, FindingBasis.DIRECT_VERSION, release=release),
             RiskFinding(DEFECT, RiskStatus.UNKNOWN, FindingBasis.DEFECT_RANGE_UNKNOWN, release=release))
    assert [f.kind for f in found] == [RiskKind.NOT_APPLIED, RiskKind.APPLIED, RiskKind.APPLIED]
    # 미확인 근거는 분류하지 않는다(위험 후보로 세지 않음).
    assert risk_kinds(found) == (RiskKind.APPLIED, RiskKind.NOT_APPLIED)
    assert risk_kinds(found[2:]) == ()


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


def test_reason_card_without_findings_says_unlinked_not_safe(app):
    card = RiskReasonCard(RiskStatus.RISK, ())
    assert "세부 근거 미연결" in card.text() and not card.kind_badges


@pytest.fixture
def source():
    catalog = QueryCatalog(sites=(SiteOption("s1", "테스트 A"),), fabs=(), lines=(),
                           groups=(GroupOption("o2", "O2", "OXIDE"),))
    family = FamilyReleases(1001, tuple(ReleaseVersion(10010 + order, 1001, f"v{order}", order) for order in (1, 2, 3)))
    families = {1001: family}
    # Item 7은 v2에서 처음 포함되어 이후 버전에 누적 적용된다. v1은 미적용이다.
    items = ItemCatalog(families, (VersionItem(10012, 7, ItemState.APPLIED, TargetScope.ALL),))
    assessor = RiskAssessor(families, items, (SAFETY, DIRECT))
    records = tuple(EquipmentRecord(eid, f"RISK-{eid:03}", DAY, "테스트 A", None, "O2",
                                    (PmVersion("PM1", 101, 1001, raw),))
                    for eid, raw in ((1, "v1"), (2, "v3"), (3, "v2")))

    def evaluate(record):
        return assessor.equipment_risk(record).status

    result = DashboardSource(catalog=catalog, bindings=(GroupBinding("o2", (101,)),), records=records,
                             locations={r.equipment_id: EquipmentLocation("s1", "P1") for r in records},
                             families=families, targets={(101, 1001): 10012},
                             risks={r.equipment_id: evaluate(r) for r in records},
                             source_label="합성 · 테스트 전용", risk_evaluator=evaluate)
    result.assessor = assessor
    return result


@pytest.fixture
def window(app, source):
    result = create_window(source)
    result.resize(1600, 960)
    result.show()
    QTest.qWait(20)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def test_heatmap_badge_shows_icon_count_and_kind_breakdown(window):
    window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    assert dashboard.snapshot.risky_ids == (1, 2)
    badge = next(iter(dashboard.risk_buttons.values()))
    # '후보 n' 대신 경고 아이콘 + 대수.
    assert badge.text() == "2" and not badge.icon().isNull() and "후보" not in badge.text()
    assert "위험 후보만 2대" in badge.accessibleName()
    tip = badge.toolTip()
    assert "적용 위험 1대" in tip and "미적용 위험 1대" in tip and "근거 미연결" not in tip


def test_side_panel_keeps_kind_data_and_shows_rule_reference_in_detail_table(window):
    query = window.query_panel.apply_draft()
    dashboard = window.pages["dashboard"]
    dashboard.drilldown_requested.emit(DashboardDrilldown(query, DAY, "위험 후보", (1, 2), True))
    drawer, page = window.equipment_list_drawer, window.pages["equipment"]
    assert drawer.is_open
    model = page.table.model()
    assert model.headerData(4, Qt.Orientation.Horizontal) == "위험버전"
    assert model.headerData(5, Qt.Orientation.Horizontal) == "위험 기준"
    assert all(model.rows[row][4] == "위험 후보" for row in range(model.rowCount()))
    cells = {model.ids[row]: model.rows[row][5] for row in range(model.rowCount())}
    assert cells == {1: "미적용 위험", 2: "적용 위험"}
    tip = model.index(0, 5).data(Qt.ItemDataRole.ToolTipRole)
    assert "환경·안전 필수 Item이 적용되지 않았습니다." in tip
    # 상세는 기준별 한 행을 표시하고 위험 분류·관측 근거는 원래 판정 자료에 보존한다.
    page.open_row(0)
    assert drawer.panel.isAncestorOf(drawer.risk_table)
    assert drawer.risk_table.rows == (("안전 인터록 필수 적용", "별도 기준 버전 없음", "—", "필수 Item 미적용"),)
    findings = drawer.detail.whole.findings
    assert risk_kinds(findings) == (RiskKind.NOT_APPLIED,)
    assert findings[0].pm.version_raw == "v1" and findings[0].basis is FindingBasis.SAFETY_NOT_APPLIED
    assert "환경·안전 필수 Item이 적용되지 않았습니다." == findings[0].message
    drawer.back_button.click()
    page.open_row(1)
    assert drawer.risk_table.rows == (("v3 통신 오류", "v3", "—", "버전 일치"),)
    assert risk_kinds(drawer.detail.whole.findings) == (RiskKind.APPLIED,)
    assert drawer.detail.whole.findings[0].basis is FindingBasis.DIRECT_VERSION
    assert not hasattr(drawer, "risk_reasons")


def test_general_scope_keeps_whole_risk_column_and_no_reason_card_for_safe(window):
    query = window.query_panel.apply_draft()
    window.pages["dashboard"].drilldown_requested.emit(DashboardDrilldown(query, DAY, "전체", (1, 2, 3), False))
    page = window.pages["equipment"]
    assert page.table.model().headings == ("설비명", "모델", "계열", "적용현황", "위험버전")
    page.open_row(page.table.model().ids.index(3))
    assert window.equipment_list_drawer.risk_table is None
    assert window.equipment_list_drawer.detail.whole.risk is RiskStatus.NO_RISK
