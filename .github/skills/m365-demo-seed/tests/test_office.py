import io
import unittest
from xml.etree import ElementTree
import zipfile

from support import WorkspaceCase  # noqa: F401 - puts the scripts folder on sys.path

from m365demo import office, sample

ADMIN = "admin@contoso-demo.example"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def package(data):
    archive = zipfile.ZipFile(io.BytesIO(data))
    parts = {name: archive.read(name) for name in archive.namelist()}
    for name, content in parts.items():
        if name.endswith((".xml", ".rels")):
            ElementTree.fromstring(content)  # every part is well-formed XML
    return archive.namelist(), parts


def relationship_targets(parts):
    """Every internal relationship target must exist in the package (targets are relative to the source part)."""
    missing = []
    for name, content in parts.items():
        if not name.endswith(".rels"):
            continue
        folder = name.rsplit("_rels/", 1)[0].rstrip("/")
        for rel in ElementTree.fromstring(content).iter(f"{REL}Relationship"):
            target = rel.get("Target")
            resolved = []
            for piece in f"{folder}/{target}".split("/"):
                if piece == "..":
                    resolved.pop()
                elif piece:
                    resolved.append(piece)
            if "/".join(resolved) not in parts:
                missing.append((name, target))
    return missing


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.doc = sample.build(ADMIN, [(f"u{i}@contoso-demo.example", None) for i in range(4)])
        self.files = {item["type"]: item for item in self.doc["files"]}

    def test_every_type_is_a_complete_package(self):
        for kind, item in self.files.items():
            data, media = office.build(item, author="Megan Bowen")
            self.assertEqual(media, office.MEDIA_TYPES[kind])
            names, parts = package(data)
            self.assertEqual(names[0], "[Content_Types].xml", kind)
            self.assertEqual(relationship_targets(parts), [], kind)
            types = parts["[Content_Types].xml"].decode()
            for name in names:
                if name.endswith(".xml") and not name.startswith("["):
                    self.assertIn(f'PartName="/{name}"', types, (kind, name))
            self.assertIn(b"Megan Bowen", parts["docProps/core.xml"])

    def test_word_content(self):
        _, parts = package(office.docx(self.files["docx"]))
        body = parts["word/document.xml"].decode()
        self.assertIn('<w:pStyle w:val="Title"/>', body)
        self.assertIn("Return-to-service checks", body)
        self.assertEqual(body.count('<w:numId w:val="1"/>'), sum(len(s.get("bullets") or []) for s in
                                                                  self.files["docx"]["sections"]))

    def test_excel_content(self):
        item = {"type": "xlsx", "name": "T.xlsx", "sheets": [
            {"name": "A & B", "rows": [["Name", "Qty", "Ok", None], ["x<y", 3, True, None], [1.5, float("nan"), -2, "z"]]}]}
        _, parts = package(office.xlsx(item))
        sheet = parts["xl/worksheets/sheet1.xml"].decode()
        self.assertIn('<c r="A2" t="inlineStr"><is><t xml:space="preserve">x&lt;y</t></is></c>', sheet)
        self.assertIn('<c r="B2"><v>3</v></c>', sheet)
        self.assertIn('<c r="C2" t="b"><v>1</v></c>', sheet)
        self.assertIn('<c r="A3"><v>1.5</v></c>', sheet)
        self.assertIn('<c r="B3" t="inlineStr"><is><t xml:space="preserve">nan</t></is></c>', sheet)
        self.assertIn('s="1"', sheet.split("</row>")[0])
        self.assertIn('name="A &amp; B"', parts["xl/workbook.xml"].decode())
        self.assertEqual([office.column(i) for i in (0, 25, 26, 51, 52, 701, 702)],
                         ["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA"])

    def test_powerpoint_content(self):
        names, parts = package(office.pptx(self.files["pptx"]))
        slides = [n for n in names if n.startswith("ppt/slides/slide")]
        self.assertEqual(len(slides), len(self.files["pptx"]["slides"]))
        first = parts["ppt/slides/slide1.xml"].decode()
        self.assertIn('<p:ph type="title"/>', first)
        self.assertIn("Asset operations weekly review", first)
        self.assertIn('type="body" idx="1"', parts["ppt/slideMasters/slideMaster1.xml"].decode())
        presentation = parts["ppt/presentation.xml"].decode()
        self.assertEqual(presentation.count("<p:sldId "), len(slides))

    def test_text_is_escaped_and_control_characters_removed(self):
        item = {"type": "pptx", "name": "E.pptx", "slides": [{"title": "A <b> & \x01 \"q\"", "bullets": ["x\x0by"]}]}
        _, parts = package(office.pptx(item))
        slide = parts["ppt/slides/slide1.xml"].decode()
        self.assertIn("A &lt;b&gt; &amp;  \"q\"", slide)
        self.assertIn("<a:t>xy</a:t>", slide)


if __name__ == "__main__":
    unittest.main()
