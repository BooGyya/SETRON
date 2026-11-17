"""표시 이름은 Memory/Foundry, 입력·저장·필터 코드는 기존 대문자를 유지한다."""

import pytest
from PySide6.QtCore import Qt

from setron.features.chat.recognize import detect_areas
from setron.features.production_area import production_area_label, production_area_text, production_area_value
from setron.ui.import_demo import DemoExcel
from setron.ui.import_page import ImportPage
from test_chat_questions import ask
from test_ui_import_ver5 import draft, own, row, until  # noqa: F401


@pytest.mark.parametrize("text,code,label", [("MEMORY", "MEMORY", "Memory"), ("memory", "MEMORY", "Memory"),
    ("메모리", "MEMORY", "Memory"), ("FOUNDRY", "FOUNDRY", "Foundry"), ("Foundry", "FOUNDRY", "Foundry"),
    ("파운드리", "FOUNDRY", "Foundry")])
def test_display_aliases_preserve_storage_codes(text, code, label):
    assert production_area_value(text) == code
    assert production_area_label(text) == label


def test_unknown_values_are_not_invented():
    assert production_area_value(None) is None
    assert production_area_label(None) == ""
    assert production_area_label(None, unknown="미확인") == "미확인"
    assert production_area_value("unknown") == production_area_label("unknown") == "unknown"


@pytest.mark.parametrize("text,expected", [("메모리(MEMORY)", "Memory"), ("파운드리(FOUNDRY)", "Foundry"),
    ("O2 (파운드리)", "O2 (Foundry)"), ("C3 MEMORY", "C3 Memory"), ("Memory/Foundry", "Memory/Foundry")])
def test_classification_display_text(text, expected):
    assert production_area_text(text) == expected


def test_import_labels_editing_and_original_values_are_separate(own):
    page = own(ImportPage(excel=DemoExcel()))
    assert [page.common_area.itemText(i) for i in (1, 2)] == ["Memory", "Foundry"]
    assert [page.common_area.itemData(i) for i in (1, 2)] == ["MEMORY", "FOUNDRY"]
    page.draft = draft(row(production_area="MEMORY"))
    page.select_row(1)
    page._changed()
    index = page.row_model.index(0, page.draft.fields.index("production_area") + 2)
    assert index.data() == index.data(Qt.ItemDataRole.EditRole) == "Memory"
    assert page.row_model.setData(index, "Foundry")
    until(lambda: page.draft.row(1).value("production_area") == "FOUNDRY")
    assert page.draft.row(1).value("production_area") == "FOUNDRY"
    assert page.draft.row(1).original["production_area"] == "MEMORY"
    assert index.data() == "Foundry"
    assert "원래 값 Memory → 새 값 Foundry" in index.data(Qt.ItemDataRole.ToolTipRole)


@pytest.mark.parametrize("question", [f"화성 {name} O3 설비 몇 대야?" for name in
    ("Memory", "MEMORY", "memory", "MEmory", "mEmOrY", "메모리", "메모리(MEMORY)")])
def test_ai_accepts_both_languages_and_displays_the_same_label(question):
    result = ask(question)
    assert result.request.areas == ("MEMORY",)
    assert result.request.conditions.unresolved == ()
    assert "화성 Memory M-O3 설비는 1대입니다." in result.summary
    assert "영역 = Memory" in result.understood


def test_ai_recognizes_foundry_aliases_without_partial_english_matches():
    for word in ("Foundry", "FOUNDRY", "foundry", "FOunDrY", "파운드리", "파운드리(FOUNDRY)"):
        assert detect_areas(f"평택 {word} 설비 보여줘") == ("FOUNDRY",)
        result = ask(f"평택 {word} 설비 보여줘")
        assert result.request.areas == ("FOUNDRY",)
        assert result.request.conditions.unresolved == ()
    assert detect_areas("memoryless foundryman") == ()
