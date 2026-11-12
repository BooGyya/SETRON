"""Word 보고서의 모든 페이지에 자동 페이지 번호를 표시한다."""

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor


def add_page_numbers(document):
    """첫 페이지부터 오른쪽 아래에 PAGE 필드를 둔다. 편집·인쇄 시 실제 쪽수로 계산된다."""
    seen = set()
    for section in document.sections:
        section.footer_distance = Mm(8)
        footer = section.footer
        if footer.part.partname in seen:
            continue
        seen.add(footer.part.partname)
        paragraph = footer.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), "PAGE")
        run = paragraph.add_run()
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor.from_string("566173")
        text = OxmlElement("w:t")
        text.text = "1"
        run._r.append(text)
        field.append(run._r)
        paragraph._p.append(field)
    settings = document.settings.element
    update = settings.find(qn("w:updateFields"))
    if update is None:
        update = OxmlElement("w:updateFields")
        settings.append(update)
    update.set(qn("w:val"), "true")
