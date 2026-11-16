"""통일 예측 그래프 (11/16 멘토: 'C3 통합UI는 언제 통일될까?' 그래프가 이상하고 보고서도 같음).

가로축이 실적 선 범위로만 잡혀 예측 점선이 잘렸고, 눈금이 같은 달을 두 번 찍었으며, 범례 이름이 길게 겹쳤다.
보고서는 이 화면 그래프를 그대로 캡처하므로 화면 그래프로 확인한다.
"""

from PySide6.QtCharts import QCategoryAxis
from PySide6.QtWidgets import QApplication

from demo_like_data import CATALOG, DATA
from setron.features.chat import answer, recognize
from setron.ui.chat_page import line_chart

APP = QApplication.instance() or QApplication([])


def forecast_view():
    result = answer(recognize("C3 통합UI는 언제 통일될까?", CATALOG), DATA)
    return result.charts[0], line_chart(result.charts[0])


def test_x_axis_covers_the_projection_and_month_ticks_do_not_repeat():
    chart, view = forecast_view()
    axis = next(a for a in view.chart().axes() if isinstance(a, QCategoryAxis))
    labels = axis.categoriesLabels()
    assert len(labels) == len(set(labels)) and 2 <= len(labels) <= 9
    last_day = max(point.day for _, points in chart.series for point in points)
    first_tick = axis.categoriesLabels()[0]
    assert first_tick == min(point.day for _, points in chart.series for point in points).strftime("%y.%m")
    projected = [s for s in view.chart().series() if s.name().endswith("예측")]
    assert projected and max(p.x() for p in projected[0].points()) <= axis.max()
    assert last_day.strftime("%y.%m") <= labels[-1] or axis.max() >= max(p.x() for p in projected[0].points())


def test_legend_names_are_short():
    _, view = forecast_view()
    assert [s.name() for s in view.chart().series()] == ["C3 통합UI 실적", "C3 통합UI 예측", "전체 평균"]


# --- 11/16 AI 분석 그래프 · 질문 점검에서 함께 고친 것 ------------------------------------------------
def test_site_risk_level_is_answered_with_a_chart():
    from setron.features.chat.schemas import Intent

    result = answer(recognize("사이트별 위험도", CATALOG), DATA)
    assert result.request.intents == (Intent.AGGREGATE,) and result.request.aggregate[:2] == ("risk", "site")
    table = result.tables[0]
    assert table.columns[-1] == "위험도" and {row[0] for row in table.rows} == {"화성", "평택"}
    assert result.charts[0].title == "사업장별 위험도 (0~100)" and result.charts[0].highlight == (0,)
    assert answer(recognize("평택 라인별 위험도 알려줘", CATALOG), DATA).tables[0].rows[0][0].startswith("평택 ")


def test_two_month_comparison_uses_only_those_months():
    result = answer(recognize("9월이랑 10월 비교", CATALOG), DATA)
    assert "2026-09-21" in result.summary and "2026-10-21" in result.summary and "(2개 기준일)" in result.summary


def test_trend_without_a_measure_word_is_the_achievement_trend():
    result = answer(recognize("평택 추이 보여줘", CATALOG), DATA)
    assert result.request.aggregate == ("achievement", "date", "") and result.charts[0].title == "기준일별 목표 N 달성률"


def test_forecast_without_progress_says_it_cannot_project():
    result = answer(recognize("버전 통일률이 가장 낮은 라인을 분석해줘", CATALOG), DATA)
    line = next(c for c in result.charts if "달성률 추이" in c.title)
    assert line.title.endswith("(진척이 없어 통일 예측 불가)")


def test_many_bars_are_drawn_horizontally_without_empty_items():
    from PySide6.QtCharts import QHorizontalStackedBarSeries

    from setron.ui.chat_page import bar_chart

    chart = answer(recognize("라인별 달성률 보여줘", CATALOG), DATA).charts[0]
    view = bar_chart(chart)
    series = view.chart().series()[0]
    assert isinstance(series, QHorizontalStackedBarSeries) and series.isLabelsVisible()
    assert series.barSets()[0].count() == sum(1 for v in chart.values if v is not None)
