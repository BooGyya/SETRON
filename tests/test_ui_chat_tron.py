"""AI 분석의 별 아이콘을 보조 캐릭터 '트론'으로 (11/11). 처음 화면 디자인 잠금(EmptyState)은 그대로 둔다."""

from PySide6.QtWidgets import QLabel

from setron.ui.chat_components import TRON, avatar
from setron.ui.chat_page import ChatPage
from test_chat_phrasing import Service
from test_ui_chat_ver5 import ui  # noqa: F401  (fixture)


def test_tron_assets_exist_with_transparent_background():
    from PySide6.QtGui import QImage
    for name in ("tron-welcome", "tron-wait", "tron-face"):
        image = QImage(str(TRON / f"{name}.png"))
        assert not image.isNull(), name
        assert image.hasAlphaChannel() and image.pixelColor(0, 0).alpha() == 0, name     # 바깥 배경은 투명


def test_answer_avatar_is_round_tron_face(ui):
    widget = ui(avatar())
    assert widget.property("tronImage") == "tron/tron-face.png" and not widget.pixmap().isNull()
    assert widget.width() == widget.height() == 36


def test_empty_state_hero_is_waving_tron_before_and_after_new_conversation(ui):
    page = ui(ChatPage(Service(None)))
    hero = lambda: page.empty.findChild(QLabel, "aiHero")
    assert hero().property("tronImage") == "tron/tron-welcome.png" and not hero().pixmap().isNull()
    page.new_conversation(ask=False)
    assert hero().property("tronImage") == "tron/tron-welcome.png"
