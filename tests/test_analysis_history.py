"""AI 원인 설명에 버전 변경 이력을 보낸다 (BE-019 · FUN-038)."""

from datetime import date

from setron.features.analysis.schemas import AnalysisInput
from setron.features.analysis.service import build_facts, version_changes
from test_analysis_service import DAY1, DAY2, FAMILIES, NAMES, TARGETS, equipment, o2

DAY3 = date(2026, 11, 3)


def test_changes_between_neighbouring_observations_with_direction():
    history = (
        equipment(1, "평택", o2("v2.1"), day=DAY1), equipment(1, "평택", o2("v2.3"), day=DAY2),  # 올림
        equipment(2, "평택", o2("v2.3"), day=DAY1), equipment(2, "평택", o2("v2.2"), day=DAY2),  # 내림
        equipment(3, "평택", o2("v2.2"), day=DAY1), equipment(3, "평택", o2("v9.9"), day=DAY2),  # 등록 안 된 버전
        equipment(4, "평택", o2("v2.2"), day=DAY1), equipment(4, "평택", o2("v2.2"), day=DAY2),  # 변경 없음
        equipment(5, "평택", o2("v2.1"), day=DAY1, hpa=True), equipment(5, "평택", o2("v2.3"), day=DAY2, hpa=True),
    )

    result = version_changes(history, FAMILIES, NAMES)

    assert (result["변경 건수"], result["올림 건수"], result["내림 건수"], result["순서 미확인 건수"]) == (3, 1, 1, 1)
    assert result["관측일 수"] == 2 and result["비교 기간"] == {"처음": DAY1.isoformat(), "끝": DAY2.isoformat()}
    first = result["변경"][0]
    assert (first["설비"], first["계열"], first["이전 버전"], first["다음 버전"], first["방향"]) == (
        "DM-001", "O2", "v2.1", "v2.3", "올림")


def test_only_neighbouring_observed_dates_are_compared():
    """관측이 없는 날은 채우지 않고, 관측된 이웃 날짜끼리만 비교한다."""
    history = (equipment(1, "평택", o2("v2.1"), day=DAY1), equipment(1, "평택", o2("v2.3"), day=DAY3))
    result = version_changes(history, FAMILIES, NAMES)
    assert result["변경"][0]["이전 관측일"] == DAY1.isoformat() and result["변경"][0]["다음 관측일"] == DAY3.isoformat()


def test_facts_include_history_only_when_given():
    records = (equipment(1, "평택", o2("v2.3")),)
    base = AnalysisInput(records=records, families=FAMILIES, targets=TARGETS, family_names=NAMES)
    assert "버전 변경 이력" not in build_facts(base)
    with_history = AnalysisInput(records=records, families=FAMILIES, targets=TARGETS, family_names=NAMES,
                                 history=(equipment(1, "평택", o2("v2.1"), day=DAY1), *records))
    assert build_facts(with_history)["버전 변경 이력"]["올림 건수"] == 1
