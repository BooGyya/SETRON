"""PDF 미리보기·다운로드·인쇄의 공통 페이지 번호. 본문과 번호 영역을 분리한다."""

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrinter


DOCUMENT_DPI = 96
FOOTER_MM = 8


def _pixels(mm):
    return mm * DOCUMENT_DPI / 25.4


def configure_report_document(document):
    """화면 배율과 무관한 A4 본문 크기. 번호는 본문 아래 여백에 그린다."""
    document.setDocumentMargin(0)
    document.setPageSize(QSizeF(_pixels(178), _pixels(265)))


def configure_report_device(device):
    device.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    # 본문 아래 16mm는 번호 영역 8mm와 종이 끝 여백 8mm로 나눈다.
    device.setPageMargins(QMarginsF(16, 16, 16, 8), QPageLayout.Unit.Millimeter)


def print_numbered_document(document, device):
    """실제 페이지별로 본문과 오른쪽 하단 번호를 그린다. 선택한 인쇄 범위도 유지한다."""
    if isinstance(device, QPrinter):
        device.setFullPage(False)
    rect = device.pageLayout().paintRect(QPageLayout.Unit.Millimeter)
    width, height = _pixels(rect.width()), _pixels(rect.height() - FOOTER_MM)
    if width <= 0 or height <= 0:
        raise OSError("보고서를 인쇄할 용지 크기와 여백을 확인해 주세요.")
    document.setPageSize(QSizeF(width, height))
    ranges = device.pageRanges()
    pages = [page for page in range(1, document.pageCount() + 1)
             if ranges.isEmpty() or ranges.contains(page)]
    if isinstance(device, QPrinter) and device.pageOrder() == QPrinter.PageOrder.LastPageFirst:
        pages.reverse()
    if not pages:
        raise OSError("선택한 인쇄 범위에 보고서 페이지가 없습니다.")
    painter = QPainter()
    if not painter.begin(device):
        raise OSError("보고서를 출력할 수 없습니다. 파일이나 프린터 상태를 확인해 주세요.")
    try:
        painter.scale(device.logicalDpiX() / DOCUMENT_DPI, device.logicalDpiY() / DOCUMENT_DPI)
        font = QFont("맑은 고딕")
        font.setPixelSize(12)
        for index, page in enumerate(pages):
            if index and not device.newPage():
                raise OSError("보고서의 다음 페이지를 출력하지 못했습니다.")
            top = (page - 1) * height
            painter.save()
            painter.setClipRect(QRectF(0, 0, width, height))
            painter.translate(0, -top)
            document.drawContents(painter, QRectF(0, top, width, height))
            painter.restore()
            painter.setFont(font)
            painter.setPen(QColor("#566173"))
            painter.drawText(QRectF(0, height, width, _pixels(FOOTER_MM)),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(page))
    finally:
        painter.end()
