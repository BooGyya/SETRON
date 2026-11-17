"""2차 화면 점검(07_점검 버그 목록 10~14·18번) 표시 수정을 검증한다."""

import os
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from setron.features.version.schemas import ReleaseVersion
from setron.ui.app import create_window
from setron.ui.dashboard_data import count_value, family_name, model_name, upgrade_caption
from setron.ui.dashboard_demo import demo_dashboard_source
from setron.ui.database_source import DatabaseDashboardSource
from setron.ui.query_state import QuerySelection
from setron.ui.theme import install_theme
from setron.ui.version_data import ModelFamily, ReleaseEntry, VersionCatalog


@pytest.fixture(scope="module")
def app():
    result = QApplication.instance() or QApplication([])
    install_theme(result)
    yield result


def test_unknown_counts_are_not_shown_as_zero():
    """판단하지 못한 대상이 있으면 0대 대신 미확인·판단 불가를 크게 보인다 (12번)."""
    assert count_value(0, 3, "미확인") == "미확인"
    assert count_value(0, 0, "미확인") == "0대"
    assert count_value(2, 3, "미확인") == "2대"  # 확인된 대수는 그대로, 미확인 수는 설명 줄
    assert upgrade_caption("기본", 2) == "기본 · 판단 불가 2건" and upgrade_caption("기본", 0) == "기본"


def test_family_and_model_names_fall_back_to_numbers():
    """DB 공급자의 이름이 있으면 이름, 없으면 번호를 보인다 (10번)."""
    source = demo_dashboard_source()
    family = next(iter(source.families))
    assert family_name(source, family) == str(family)
    model = next(m for ids in source.bindings.values() for m in ids)
    assert model_name(source, model) != f"모델 {model}"  # 설비군 이름으로 찾는다
    assert model_name(source, -1) == "모델 -1"

    named = SimpleNamespace(family_names={3: "C4"}, model_names={2: "M-C4"}, catalog=source.catalog, bindings={})
    assert family_name(named, 3) == "C4" and model_name(named, 2) == "M-C4"


def test_result_badge_says_example_only_for_synthetic_source(app):
    """합성 자료는 '예시 결과', DB 자료는 'DB 자료'로 배지를 단다 (11번)."""
    assert DatabaseDashboardSource.result_label == "DB 자료"
    window = create_window()
    try:
        window.query_panel.apply_draft()
        assert window.pages["dashboard"].result_status.text().startswith("예시 결과")
    finally:
        window.close()


def test_versions_first_open_selects_family_with_releases(app):
    """처음 열면 릴리즈가 있는 첫 모델·계열을 고른다 (14번)."""
    catalog = VersionCatalog(
        (ModelFamily(1, "빈 모델", 10, "빈 계열"), ModelFamily(2, "M-C4", 20, "C3 구UI"), ModelFamily(2, "M-C4", 30, "C4")),
        (ReleaseEntry(ReleaseVersion(301, 30, "v1.0.3", 10, date(2026, 9, 1)), (2,)),),
    )
    window = create_window(version_loader=lambda: catalog)
    try:
        window.show()
        window.navigate("versions")
        QTest.qWait(50)
        page = window.pages["versions"]
        assert (page.model_filter.currentData(), page.family_filter.currentData()) == (2, 30)
        assert page.list_model.rowCount() == 1
    finally:
        window.close()


def test_heatmap_tiles_keep_height_in_tall_window(app):
    """창이 내용보다 커도(4K 등) 타일이 세로로 늘어나지 않는다 (18번)."""
    heights = {}
    for size in ((1440, 920), (1440, 4000)):
        window = create_window()
        try:
            window.resize(*size)
            window.show()
            window.query_panel.set_draft(QuerySelection())
            window.query_panel.apply_draft()
            window.navigate("heatmap")
            QTest.qWait(100)
            heights[size] = {button.height() for button in window.pages["heatmap"].tile_buttons.values()}
        finally:
            window.close()
    assert heights[(1440, 4000)] == heights[(1440, 920)]
