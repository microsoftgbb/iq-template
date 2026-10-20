"""Word, Excel and PowerPoint files from structured content, as minimal Office Open XML packages.

Standard library only (zipfile). Each package carries the parts Office requires and nothing
else: content types, relationships, document properties and the main parts.
"""

from datetime import datetime, timezone
import io
import math
from xml.sax.saxutils import escape
import zipfile

MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def x(value):
    """Text safe for XML element content (control characters removed)."""
    cleaned = "".join(ch for ch in str(value) if ch in "\t\n" or ord(ch) >= 32)
    return escape(cleaned)


def attr(value):
    return escape(str(value), {'"': "&quot;"})


def _package(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # [Content_Types].xml first, as Office writes it.
        archive.writestr("[Content_Types].xml", parts.pop("[Content_Types].xml"))
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _types(defaults, overrides):
    body = "".join(f'<Default Extension="{ext}" ContentType="{kind}"/>' for ext, kind in defaults)
    body += "".join(f'<Override PartName="{part}" ContentType="{kind}"/>' for part, kind in overrides)
    return f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">{body}</Types>'


def _rels(items):
    body = "".join(f'<Relationship Id="{rid}" Type="{kind}" Target="{target}"/>' for rid, kind, target in items)
    return f'{XML}<Relationships xmlns="{PKG_REL}">{body}</Relationships>'


def _core(title, author):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (f'{XML}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{x(title)}</dc:title><dc:creator>{x(author)}</dc:creator>"
            f"<cp:lastModifiedBy>{x(author)}</cp:lastModifiedBy>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')


def _app(application):
    return (f'{XML}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            f"<Application>{x(application)}</Application></Properties>")


def _common_parts(main_part, main_type, title, author, application, extra_overrides=()):
    overrides = [(f"/{main_part}", main_type),
                 ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                 ("/docProps/app.xml", f"{CT}.extended-properties+xml"), *extra_overrides]
    return {
        "[Content_Types].xml": _types([("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                                       ("xml", "application/xml")], overrides),
        "_rels/.rels": _rels([("rId1", f"{REL}/officeDocument", main_part),
                              ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/"
                                       "core-properties", "docProps/core.xml"),
                              ("rId3", f"{REL}/extended-properties", "docProps/app.xml")]),
        "docProps/core.xml": _core(title, author),
        "docProps/app.xml": _app(application),
    }


# --- Word -------------------------------------------------------------------------------------

def docx(item, author="m365-demo-seed"):
    title = item.get("title") or item["name"].rsplit(".", 1)[0]
    paragraphs = [_w_paragraph(title, "Title")]
    for section in item.get("sections") or []:
        if section.get("heading"):
            paragraphs.append(_w_paragraph(section["heading"], "Heading1"))
        paragraphs += [_w_paragraph(value, None) for value in section.get("paragraphs") or []]
        paragraphs += [_w_paragraph(value, "ListParagraph", bullet=True) for value in section.get("bullets") or []]
    document = (f'{XML}<w:document xmlns:w="{W}" xmlns:r="{REL}"><w:body>{"".join(paragraphs)}'
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
                '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" '
                'w:gutter="0"/></w:sectPr></w:body></w:document>')
    parts = _common_parts("word/document.xml", f"{CT}.wordprocessingml.document.main+xml", title, author,
                          "Microsoft Office Word",
                          [("/word/styles.xml", f"{CT}.wordprocessingml.styles+xml"),
                           ("/word/numbering.xml", f"{CT}.wordprocessingml.numbering+xml")])
    parts.update({
        "word/document.xml": document,
        "word/_rels/document.xml.rels": _rels([("rId1", f"{REL}/styles", "styles.xml"),
                                               ("rId2", f"{REL}/numbering", "numbering.xml")]),
        "word/styles.xml": _WORD_STYLES,
        "word/numbering.xml": _WORD_NUMBERING,
    })
    return _package(parts)


def _w_paragraph(value, style, bullet=False):
    properties = ""
    if style or bullet:
        properties = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "")
        properties += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if bullet else ""
        properties += "</w:pPr>"
    lines = str(value).split("\n")
    runs = '<w:r><w:br/></w:r>'.join(f'<w:r><w:t xml:space="preserve">{x(line)}</w:t></w:r>' for line in lines)
    return f"<w:p>{properties}{runs}</w:p>"


_WORD_STYLES = (
    f'{XML}<w:styles xmlns:w="{W}">'
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" '
    'w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
    '</w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="240"/></w:pPr>'
    '<w:rPr><w:sz w:val="48"/><w:szCs w:val="48"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/>'
    '<w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="2F5496"/><w:sz w:val="32"/><w:szCs w:val="32"/>'
    '</w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/>'
    '<w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>'
    '</w:styles>')

_WORD_NUMBERING = (
    f'{XML}<w:numbering xmlns:w="{W}">'
    '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="\u2022"/>'
    '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')


# --- Excel ------------------------------------------------------------------------------------

def column(index):
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell(reference, value, header):
    style = ' s="1"' if header else ""
    if value is None:
        return ""
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"{style}><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f'<c r="{reference}"{style}><v>{value!r}</v></c>'
    return f'<c r="{reference}" t="inlineStr"{style}><is><t xml:space="preserve">{x(value)}</t></is></c>'


def _sheet(rows):
    width = max((len(row) for row in rows), default=1)
    widths = [10] * width
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = min(60, max(widths[index], len(str(value)) + 2 if value is not None else 0))
    columns = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    data = "".join(
        f'<row r="{r + 1}">' + "".join(_cell(f"{column(c)}{r + 1}", value, r == 0) for c, value in enumerate(row))
        + "</row>" for r, row in enumerate(rows))
    pane = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            'state="frozen"/></sheetView></sheetViews>') if len(rows) > 1 else ""
    return (f'{XML}<worksheet xmlns="{S}" xmlns:r="{REL}">{pane}<cols>{columns}</cols>'
            f'<sheetData>{data}</sheetData></worksheet>')


def xlsx(item, author="m365-demo-seed"):
    sheets = item.get("sheets") or []
    title = item["name"].rsplit(".", 1)[0]
    entries = "".join(f'<sheet name="{attr(sheet["name"])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                      for i, sheet in enumerate(sheets))
    workbook = (f'{XML}<workbook xmlns="{S}" xmlns:r="{REL}"><bookViews><workbookView/></bookViews>'
                f"<sheets>{entries}</sheets></workbook>")
    relations = [(f"rId{i + 1}", f"{REL}/worksheet", f"worksheets/sheet{i + 1}.xml") for i in range(len(sheets))]
    relations.append((f"rId{len(sheets) + 1}", f"{REL}/styles", "styles.xml"))
    overrides = [(f"/xl/worksheets/sheet{i + 1}.xml", f"{CT}.spreadsheetml.worksheet+xml") for i in range(len(sheets))]
    overrides.append(("/xl/styles.xml", f"{CT}.spreadsheetml.styles+xml"))
    parts = _common_parts("xl/workbook.xml", f"{CT}.spreadsheetml.sheet.main+xml", title, author,
                          "Microsoft Excel", overrides)
    parts.update({"xl/workbook.xml": workbook, "xl/_rels/workbook.xml.rels": _rels(relations),
                  "xl/styles.xml": _EXCEL_STYLES})
    for index, sheet in enumerate(sheets):
        parts[f"xl/worksheets/sheet{index + 1}.xml"] = _sheet(sheet.get("rows") or [])
    return _package(parts)


_EXCEL_STYLES = (
    f'{XML}<styleSheet xmlns="{S}">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/><bgColor indexed="64"/></patternFill></fill>'
    '</fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


# --- PowerPoint -------------------------------------------------------------------------------

SLIDE_W, SLIDE_H = 12192000, 6858000
TITLE_BOX = (838200, 365125, 10515600, 1325563)
BODY_BOX = (838200, 1825625, 10515600, 4351338)


def _xfrm(box):
    left, top, width, height = box
    return f'<a:xfrm><a:off x="{left}" y="{top}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'


def _group_tree(shapes):
    return ('<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
            f'<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>{shapes}</p:spTree>')


def _placeholder(shape_id, name, kind, box=None, paragraphs=None):
    ph = {"title": '<p:ph type="title"/>', "body": '<p:ph type="body" idx="1"/>', "content": '<p:ph idx="1"/>'}[kind]
    properties = f"<p:spPr>{_xfrm(box)}</p:spPr>" if box else "<p:spPr/>"
    body = "".join(paragraphs) if paragraphs else '<a:p><a:endParaRPr lang="en-US"/></a:p>'
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{name}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            f"<p:nvPr>{ph}</p:nvPr></p:nvSpPr>{properties}"
            f"<p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody></p:sp>")


def _a_paragraph(value):
    return f'<a:p><a:r><a:rPr lang="en-US" dirty="0"/><a:t>{x(value)}</a:t></a:r></a:p>'


def _slide(slide):
    shapes = _placeholder(2, "Title 1", "title", paragraphs=[_a_paragraph(slide["title"])])
    bullets = slide.get("bullets") or []
    if bullets:
        shapes += _placeholder(3, "Content Placeholder 2", "content", paragraphs=[_a_paragraph(b) for b in bullets])
    return (f'{XML}<p:sld xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}"><p:cSld>{_group_tree(shapes)}</p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


def pptx(item, author="m365-demo-seed"):
    slides = item.get("slides") or []
    title = slides[0]["title"] if slides else item["name"].rsplit(".", 1)[0]
    count = len(slides)
    slide_ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 2}"/>' for i in range(count))
    presentation = (
        f'{XML}<p:presentation xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}" saveSubsetFonts="1">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f"<p:sldIdLst>{slide_ids}</p:sldIdLst>"
        f'<p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}"/><p:notesSz cx="6858000" cy="9144000"/>'
        "<p:defaultTextStyle>" + _LEVEL1.format(tag="lvl1pPr") + "</p:defaultTextStyle></p:presentation>")
    relations = [("rId1", f"{REL}/slideMaster", "slideMasters/slideMaster1.xml")]
    relations += [(f"rId{i + 2}", f"{REL}/slide", f"slides/slide{i + 1}.xml") for i in range(count)]
    relations += [(f"rId{count + 2}", f"{REL}/theme", "theme/theme1.xml"),
                  (f"rId{count + 3}", f"{REL}/presProps", "presProps.xml"),
                  (f"rId{count + 4}", f"{REL}/viewProps", "viewProps.xml"),
                  (f"rId{count + 5}", f"{REL}/tableStyles", "tableStyles.xml")]
    overrides = [("/ppt/slideMasters/slideMaster1.xml", f"{CT}.presentationml.slideMaster+xml"),
                 ("/ppt/slideLayouts/slideLayout1.xml", f"{CT}.presentationml.slideLayout+xml"),
                 ("/ppt/theme/theme1.xml", f"{CT}.theme+xml"),
                 ("/ppt/presProps.xml", f"{CT}.presentationml.presProps+xml"),
                 ("/ppt/viewProps.xml", f"{CT}.presentationml.viewProps+xml"),
                 ("/ppt/tableStyles.xml", f"{CT}.presentationml.tableStyles+xml")]
    overrides += [(f"/ppt/slides/slide{i + 1}.xml", f"{CT}.presentationml.slide+xml") for i in range(count)]
    parts = _common_parts("ppt/presentation.xml", f"{CT}.presentationml.presentation.main+xml", title, author,
                          "Microsoft Office PowerPoint", overrides)
    parts.update({
        "ppt/presentation.xml": presentation,
        "ppt/_rels/presentation.xml.rels": _rels(relations),
        "ppt/slideMasters/slideMaster1.xml": _MASTER,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels([
            ("rId1", f"{REL}/slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", f"{REL}/theme", "../theme/theme1.xml")]),
        "ppt/slideLayouts/slideLayout1.xml": _LAYOUT,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels([
            ("rId1", f"{REL}/slideMaster", "../slideMasters/slideMaster1.xml")]),
        "ppt/theme/theme1.xml": _THEME,
        "ppt/presProps.xml": f'{XML}<p:presentationPr xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}"/>',
        "ppt/viewProps.xml": (f'{XML}<p:viewPr xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}">'
                              '<p:normalViewPr><p:restoredLeft sz="15620"/><p:restoredTop sz="94660"/></p:normalViewPr>'
                              '<p:gridSpacing cx="76200" cy="76200"/></p:viewPr>'),
        "ppt/tableStyles.xml": (f'{XML}<a:tblStyleLst xmlns:a="{A}" '
                                'def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>'),
    })
    for index, slide in enumerate(slides):
        parts[f"ppt/slides/slide{index + 1}.xml"] = _slide(slide)
        parts[f"ppt/slides/_rels/slide{index + 1}.xml.rels"] = _rels([
            ("rId1", f"{REL}/slideLayout", "../slideLayouts/slideLayout1.xml")])
    return _package(parts)


_LEVEL1 = ('<a:{tag} marL="0" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1">'
           '<a:defRPr sz="1800" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
           '<a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:{tag}>')

_MASTER = (
    f'{XML}<p:sldMaster xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}"><p:cSld><p:bg><p:bgRef idx="1001">'
    '<a:schemeClr val="bg1"/></p:bgRef></p:bg>'
    + _group_tree(_placeholder(2, "Title Placeholder 1", "title", TITLE_BOX)
                  + _placeholder(3, "Text Placeholder 2", "body", BODY_BOX)) +
    '</p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
    'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    '<p:txStyles><p:titleStyle><a:lvl1pPr algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" '
    'hangingPunct="1"><a:lnSpc><a:spcPct val="90000"/></a:lnSpc><a:spcBef><a:spcPct val="0"/></a:spcBef>'
    '<a:buNone/><a:defRPr sz="4400" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
    '<a:latin typeface="+mj-lt"/><a:ea typeface="+mj-ea"/><a:cs typeface="+mj-cs"/></a:defRPr></a:lvl1pPr>'
    '</p:titleStyle><p:bodyStyle><a:lvl1pPr marL="228600" indent="-228600" algn="l" defTabSz="914400" rtl="0" '
    'eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:lnSpc><a:spcPct val="90000"/></a:lnSpc><a:spcBef>'
    '<a:spcPts val="1000"/></a:spcBef><a:buFont typeface="Arial" panose="020B0604020202020204" pitchFamily="34" '
    'charset="0"/><a:buChar char="\u2022"/><a:defRPr sz="2800" kern="1200"><a:solidFill><a:schemeClr val="tx1"/>'
    '</a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr>'
    '</a:lvl1pPr></p:bodyStyle><p:otherStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr>'
    + _LEVEL1.format(tag="lvl1pPr") + '</p:otherStyle></p:txStyles></p:sldMaster>')

_LAYOUT = (
    f'{XML}<p:sldLayout xmlns:a="{A}" xmlns:r="{REL}" xmlns:p="{P}" type="obj" preserve="1">'
    '<p:cSld name="Title and Content">'
    + _group_tree(_placeholder(2, "Title 1", "title") + _placeholder(3, "Content Placeholder 2", "content")) +
    '</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')


def _scheme_color(name, value):
    return f"<a:{name}><a:srgbClr val=\"{value}\"/></a:{name}>"


_THEME = (
    f'{XML}<a:theme xmlns:a="{A}" name="Office Theme"><a:themeElements>'
    '<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    + _scheme_color("dk2", "44546A") + _scheme_color("lt2", "E7E6E6") + _scheme_color("accent1", "4472C4")
    + _scheme_color("accent2", "ED7D31") + _scheme_color("accent3", "A5A5A5") + _scheme_color("accent4", "FFC000")
    + _scheme_color("accent5", "5B9BD5") + _scheme_color("accent6", "70AD47") + _scheme_color("hlink", "0563C1")
    + _scheme_color("folHlink", "954F72") +
    '</a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/>'
    '<a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/>'
    '<a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="Office"><a:fillStyleLst>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst>'
    '<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    '<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
    '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
    '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements>'
    '<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>')


def build(item, author="m365-demo-seed"):
    """(bytes, media type) for a demo file entry."""
    writer = {"docx": docx, "xlsx": xlsx, "pptx": pptx}[item["type"]]
    return writer(item, author), MEDIA_TYPES[item["type"]]
