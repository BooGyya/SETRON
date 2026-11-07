"""Word(docx) 한글 글꼴 지정.

python-docx 기본 서식은 한글(동아시아) 글꼴을 테마 글꼴(minorEastAsia → MS Mincho)에 맡겨서, 한글이 없는 PC·글꼴에서
본문·표의 한글이 네모(□)로 깨진다. 문서의 모든 문단·문자·표 서식에 한글 글꼴을 직접 지정한다.
"""

from __future__ import annotations

from docx.oxml.ns import qn
from docx.oxml import OxmlElement

KOREAN_FONT = "맑은 고딕"          # Windows 기본 한글 글꼴 (Malgun Gothic)


def use_korean_font(document, font: str = KOREAN_FONT) -> None:
    """문서 서식 전체(본문·제목·표·목록 등)의 라틴·한글 글꼴을 ``font``로 지정한다."""
    for style in document.styles:
        element = getattr(style, "element", None)
        if element is None or not hasattr(style, "font"):
            continue
        style.font.name = font
        rpr = element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.append(fonts)
        for key in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            fonts.set(qn(key), font)
        for key in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            fonts.attrib.pop(qn(key), None)
