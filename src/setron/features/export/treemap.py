"""저장한 xlsx에 Excel 트리맵 차트(chartEx)를 넣는다 (명세 BE-021).

openpyxl은 트리맵(Excel 2016 이상의 chartEx 차트)을 만들지 못한다. 그래서 openpyxl로 표를 저장한 뒤
xlsx(zip)에 아래 부품을 직접 더한다. 차트는 같은 시트의 표 셀을 참조하므로 Excel에서 셀을 고치면 차트도 바뀐다.

- ``xl/charts/chartExN.xml``: 트리맵 정의. 범주는 두 단계(상위 → 하위), 크기는 건수 열
- ``xl/workbook.xml``의 숨은 이름 ``_xlchart.v1.N``: 차트가 참조하는 셀 범위 (Excel이 직접 만든 파일과 같은 방식)
- ``xl/drawings/drawingN.xml``: 차트를 놓을 위치. Excel 2016 미만에서는 안내 도형이 대신 보인다(이미지로 대체하지 않음)
- ``xl/charts/styleN.xml``·``colorsN.xml``: 차트 스타일·색. Excel은 트리맵에 이 두 부품이 없으면 파일을 열지 못한다
  (11-04 Excel 16.0 확인). 내용은 Excel이 만든 기본값(``chartex/style.xml``·``colors.xml``)을 그대로 쓴다.
- 시트·그리기·차트 관계(rels)와 ``[Content_Types].xml`` 등록

정한 규칙
- 이미 그리기(drawing)가 있는 시트에는 넣지 않는다. 트리맵 전용 시트에만 넣는다.
- 실패하면 예외를 그대로 올려 호출하는 쪽이 저장 실패와 원인을 알린다.
"""

from __future__ import annotations

import os
import re
import zipfile
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.sax.saxutils import escape

REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING_TYPE = f"{REL}/drawing"
CHARTEX_TYPE = "http://schemas.microsoft.com/office/2014/relationships/chartEx"
CHARTEX_CONTENT = "application/vnd.ms-office.chartex+xml"
STYLE_TYPE = "http://schemas.microsoft.com/office/2011/relationships/chartStyle"
COLORS_TYPE = "http://schemas.microsoft.com/office/2011/relationships/chartColorStyle"
STYLE_CONTENT = "application/vnd.ms-office.chartstyle+xml"
COLORS_CONTENT = "application/vnd.ms-office.chartcolorstyle+xml"
TEMPLATES = Path(__file__).with_name("chartex")
DRAWING_CONTENT = "application/vnd.openxmlformats-officedocument.drawing+xml"


def add_treemap(path: str | Path, sheet_title: str, rows: Sequence[tuple[str, str, float]], *, title: str,
                colors: dict[str, str] | None = None, anchor: tuple[int, int, int, int] = (4, 0, 16, 24)) -> None:
    """``sheet_title`` 시트의 A2:C(n+1) 표(상위 범주, 하위 범주, 크기)로 트리맵을 넣는다.

    ``rows``는 표에 이미 적은 값과 같아야 한다(차트 캐시). ``colors``는 하위 범주 이름 → RGB(예: "2E7D32")로
    점마다 색을 정한다. ``anchor``는 (왼쪽 열, 위 행, 오른쪽 열, 아래 행) 0부터 센 셀 위치다.
    """
    if not rows:
        raise ValueError("트리맵에 넣을 자료가 없습니다.")
    path = Path(path)
    with zipfile.ZipFile(path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    sheet_part = _sheet_part(parts, sheet_title)
    sheet_rels = _rels_name(sheet_part)
    sheet_xml = parts[sheet_part].decode("utf-8")
    if "<drawing " in sheet_xml:
        raise ValueError(f"'{sheet_title}' 시트에 이미 그리기가 있어 트리맵을 넣지 않았습니다.")
    drawing_no = _next_number(parts, "xl/drawings/drawing", ".xml")
    chart_no = _next_number(parts, "xl/charts/chartEx", ".xml")
    drawing_part, chart_part = f"xl/drawings/drawing{drawing_no}.xml", f"xl/charts/chartEx{chart_no}.xml"
    style_no, colors_no = _next_number(parts, "xl/charts/style", ".xml"), _next_number(parts, "xl/charts/colors", ".xml")
    style_part, colors_part = f"xl/charts/style{style_no}.xml", f"xl/charts/colors{colors_no}.xml"

    rel_id = _add_relationship(parts, sheet_rels, DRAWING_TYPE, f"../drawings/drawing{drawing_no}.xml")
    parts[sheet_part] = _insert_drawing(sheet_xml, rel_id).encode("utf-8")
    parts[_rels_name(drawing_part)] = _relationships([("rId1", CHARTEX_TYPE, f"../charts/chartEx{chart_no}.xml")])
    parts[drawing_part] = _drawing_xml(anchor).encode("utf-8")
    names = _add_chart_names(parts, sheet_title, len(rows))
    parts[chart_part] = _chart_xml(names, rows, title, colors or {}).encode("utf-8")
    parts[style_part] = (TEMPLATES / "style.xml").read_bytes()
    parts[colors_part] = (TEMPLATES / "colors.xml").read_bytes()
    parts[_rels_name(chart_part)] = _relationships([("rId1", STYLE_TYPE, f"style{style_no}.xml"),
                                                    ("rId2", COLORS_TYPE, f"colors{colors_no}.xml")])
    parts["[Content_Types].xml"] = _register(parts["[Content_Types].xml"].decode("utf-8"), {
        f"/{drawing_part}": DRAWING_CONTENT, f"/{chart_part}": CHARTEX_CONTENT,
        f"/{style_part}": STYLE_CONTENT, f"/{colors_part}": COLORS_CONTENT})

    with NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
            for name, data in parts.items():
                target.writestr(name, data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sheet_part(parts, title):
    workbook = parts["xl/workbook.xml"].decode("utf-8")
    match = next((m for m in re.finditer(r"<sheet\b[^>]*/>", workbook)
                  if re.search(r'\bname="([^"]*)"', m.group(0)).group(1) == escape(title, {'"': "&quot;"})), None)
    if match is None:
        raise ValueError(f"'{title}' 시트가 없습니다.")
    rel_id = re.search(r'\br:id="([^"]+)"', match.group(0)).group(1)
    rels = parts["xl/_rels/workbook.xml.rels"].decode("utf-8")
    target = re.search(rf'<Relationship\b[^>]*\bId="{re.escape(rel_id)}"[^>]*/>', rels).group(0)
    target = re.search(r'\bTarget="([^"]+)"', target).group(1)
    return target.lstrip("/") if target.startswith("/") else f"xl/{target}"


def _add_chart_names(parts, sheet_title, count):
    """차트 범위를 숨은 이름으로 등록한다: (범주 A2:B, 계열 이름 C1, 크기 C2:C)."""
    workbook = parts["xl/workbook.xml"].decode("utf-8")
    used = [int(n) for n in re.findall(r'name="_xlchart\.v1\.(\d+)"', workbook)]
    start = max(used, default=-1) + 1
    sheet = "'" + sheet_title.replace("'", "''") + "'"
    last = count + 1
    ranges = (f"{sheet}!$A$2:$B${last}", f"{sheet}!$C$1", f"{sheet}!$C$2:$C${last}")
    names = tuple(f"_xlchart.v1.{start + i}" for i in range(len(ranges)))
    entries = "".join(f'<definedName name="{n}" hidden="1">{escape(r)}</definedName>' for n, r in zip(names, ranges))
    if "<definedNames>" in workbook:
        workbook = workbook.replace("<definedNames>", "<definedNames>" + entries, 1)
    elif "<definedNames/>" in workbook:
        workbook = workbook.replace("<definedNames/>", f"<definedNames>{entries}</definedNames>", 1)
    else:
        workbook = workbook.replace("</sheets>", f"</sheets><definedNames>{entries}</definedNames>", 1)
    parts["xl/workbook.xml"] = workbook.encode("utf-8")
    return names


def _rels_name(part):
    folder, name = part.rsplit("/", 1)
    return f"{folder}/_rels/{name}.rels"


def _next_number(parts, prefix, suffix):
    numbers = [int(name[len(prefix):-len(suffix)]) for name in parts
               if name.startswith(prefix) and name.endswith(suffix) and name[len(prefix):-len(suffix)].isdigit()]
    return max(numbers, default=0) + 1


def _relationships(items):
    body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{target}"/>' for i, t, target in items)
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG_REL}">{body}</Relationships>'.encode("utf-8")


def _add_relationship(parts, rels_name, rel_type, target):
    existing = parts.get(rels_name, b"").decode("utf-8")
    ids = set(re.findall(r'\bId="([^"]+)"', existing))
    rel_id = next(f"rId{n}" for n in range(1, len(ids) + 2) if f"rId{n}" not in ids)
    entry = f'<Relationship Id="{rel_id}" Type="{rel_type}" Target="{target}"/>'
    if existing:
        parts[rels_name] = existing.replace("</Relationships>", entry + "</Relationships>").encode("utf-8")
    else:
        parts[rels_name] = _relationships([(rel_id, rel_type, target)])
    return rel_id


def _insert_drawing(sheet_xml, rel_id):
    if f'xmlns:r="{REL}"' not in sheet_xml:
        sheet_xml = sheet_xml.replace("<worksheet ", f'<worksheet xmlns:r="{REL}" ', 1)
    element = f'<drawing r:id="{rel_id}"/>'
    # 스키마 순서: drawing은 pageMargins·pageSetup·headerFooter 뒤, legacyDrawing·tableParts·extLst 앞
    for later in ("<legacyDrawing", "<legacyDrawingHF", "<picture", "<oleObjects", "<controls",
                  "<webPublishItems", "<tableParts", "<extLst"):
        if later in sheet_xml:
            return sheet_xml.replace(later, element + later, 1)
    return sheet_xml.replace("</worksheet>", element + "</worksheet>", 1)


def _register(content_types, overrides):
    entries = "".join(f'<Override PartName="{name}" ContentType="{kind}"/>' for name, kind in overrides.items()
                      if f'PartName="{name}"' not in content_types)
    return content_types.replace("</Types>", entries + "</Types>").encode("utf-8")


def _drawing_xml(anchor):
    left, top, right, bottom = anchor
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f'<xdr:twoCellAnchor><xdr:from><xdr:col>{left}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{top}</xdr:row>'
        f'<xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>{right}</xdr:col><xdr:colOff>0</xdr:colOff>'
        f'<xdr:row>{bottom}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
        '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        '<mc:Choice xmlns:cx1="http://schemas.microsoft.com/office/drawing/2015/9/8/chartex" Requires="cx1">'
        '<xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="2" name="트리맵"/><xdr:cNvGraphicFramePr/>'
        '</xdr:nvGraphicFramePr><xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/drawing/2014/chartex">'
        f'<cx:chart xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex" xmlns:r="{REL}" r:id="rId1"/>'
        '</a:graphicData></a:graphic></xdr:graphicFrame></mc:Choice>'
        '<mc:Fallback><xdr:sp macro="" textlink=""><xdr:nvSpPr><xdr:cNvPr id="3" name="트리맵 안내"/>'
        '<xdr:cNvSpPr><a:spLocks noTextEdit="1"/></xdr:cNvSpPr></xdr:nvSpPr><xdr:spPr>'
        '<a:xfrm><a:off x="0" y="0"/><a:ext cx="4572000" cy="2743200"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '</xdr:spPr><xdr:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ko-KR" sz="1100"/>'
        '<a:t>이 트리맵은 Excel 2016 이상에서 보입니다. 표 자료는 왼쪽에 있습니다.</a:t></a:r></a:p></xdr:txBody></xdr:sp>'
        '</mc:Fallback></mc:AlternateContent><xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>'
    )


PARENT_COLOR = "5B6577"  # 상위 범주 띠


def _point_colors(rows, colors):
    """(점 번호, 색). Excel 트리맵의 점 번호는 상위 범주(처음 나온 순서)마다 그 상위 칸, 이어서 하위 칸을 표 순서로 센다.

    11-04 Excel 16.0에서 확인했다. 그래서 표는 상위 범주끼리 붙어 있어야 한다(``rows``가 상위 범주별로 묶인 순서).
    """
    index, result, seen = 0, [], []
    for parent, child, _ in rows:
        if parent not in seen:
            seen.append(parent)
            result.append((index, PARENT_COLOR))
            index += 1
        if child in colors:
            result.append((index, colors[child]))
        index += 1
    return result


def _chart_xml(names, rows, title, colors):
    category, series_name, size = names
    data_points = "".join(
        f'<cx:dataPt idx="{i}"><cx:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        '<a:ln><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:ln></cx:spPr></cx:dataPt>'
        for i, color in _point_colors(rows, colors))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cx:chartSpace xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        f'xmlns:r="{REL}" xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex">'
        '<cx:chartData><cx:data id="0">'
        f'<cx:strDim type="cat"><cx:f>{category}</cx:f></cx:strDim>'
        f'<cx:numDim type="size"><cx:f>{size}</cx:f></cx:numDim>'
        '</cx:data></cx:chartData><cx:chart>'
        f'<cx:title pos="t" align="ctr" overlay="0"><cx:tx><cx:txData><cx:v>{escape(title)}</cx:v></cx:txData></cx:tx></cx:title>'
        '<cx:plotArea><cx:plotAreaRegion><cx:series layoutId="treemap" uniqueId="{4C2E9C33-6F7B-4E0A-9D5E-5E54B1F0A001}">'
        f'<cx:tx><cx:txData><cx:f>{series_name}</cx:f><cx:v>건수</cx:v></cx:txData></cx:tx>'
        f'{data_points}'
        '<cx:dataLabels pos="inEnd"><cx:visibility seriesName="0" categoryName="1" value="1"/></cx:dataLabels>'
        '<cx:dataId val="0"/><cx:layoutPr><cx:parentLabelLayout val="banner"/></cx:layoutPr></cx:series>'
        '</cx:plotAreaRegion></cx:plotArea><cx:legend pos="t" align="ctr" overlay="0"/></cx:chart></cx:chartSpace>'
    )
