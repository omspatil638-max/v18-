"""
docx_service.py
---------------
Word (.docx) intake. A Word file has no pages, and the source viewer needs pages, so the file is
converted to a PDF (text and tables, in document order) and that PDF goes through the normal
pipeline. Page numbers therefore refer to the CONVERTED copy, which is said so in a review flag.
The original .docx is kept next to it.

What is not converted, and is reported rather than silently dropped: images, text boxes and
headers/footers. Tracked changes are read as the document currently shows them.

Safety: a .docx is a zip file. Its declared uncompressed size and entry count are checked before
anything is parsed (zip bombs), macro-enabled content is refused, and nothing in the file is ever
fetched, executed or written outside the storage folder.
"""

import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import List

import pymupdf

MAX_ENTRIES = 3000
MAX_UNCOMPRESSED = 120 * 1024 * 1024        # declared, summed over all entries
MAX_TEXT_CHARS = 3_000_000
_LIST_STYLE = re.compile(r"list|bullet|number", re.IGNORECASE)


class DocxError(ValueError):
    """The file cannot be used. The message is safe to show to the user."""


@dataclass
class DocxConversion:
    pdf: bytes
    page_count: int
    notes: List[str] = field(default_factory=list)      # what was not converted


def validate_container(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DocxError("The file is not a valid Word document (it could not be opened).")
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise DocxError("The Word file has too many parts to be a normal document.")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise DocxError("The Word file expands to an unreasonable size and was rejected.")
    names = {i.filename for i in infos}
    if "word/document.xml" not in names or "[Content_Types].xml" not in names:
        raise DocxError("The file is not a Word document (.docx).")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise DocxError("Word files that contain macros are not accepted. Save a copy without macros, or export it as PDF.")
    return zf


def _esc(text: str) -> str:
    return html.escape(text.replace("\x00", ""), quote=False)


def _to_html(data: bytes):
    from docx import Document
    from docx.oxml.ns import qn

    try:
        doc = Document(io.BytesIO(data))
    except Exception:  # noqa: BLE001 - python-docx raises many kinds of errors on bad input
        raise DocxError("The Word document could not be read. It may be damaged.")

    body = doc.element.body
    parts: List[str] = []
    chars = 0
    notes: List[str] = []
    xml = body.xml if hasattr(body, "xml") else ""
    if "<w:drawing" in xml or "<w:pict" in xml:
        notes.append("Images or drawings in the document were not converted.")
    if "txbxContent" in xml:
        notes.append("Text inside text boxes was not converted.")

    def paragraph(p) -> None:
        nonlocal chars
        text = p.text.strip()
        if not text:
            return
        chars += len(text)
        if chars > MAX_TEXT_CHARS:
            raise DocxError("The document is too long to process.")
        style = (p.style.name or "") if p.style is not None else ""
        if style == "Title" or style == "Heading 1":
            parts.append(f"<h1>{_esc(text)}</h1>")
        elif style.startswith("Heading"):
            parts.append(f"<h2>{_esc(text)}</h2>")
        elif _LIST_STYLE.search(style):
            parts.append(f"<p>&#8226; {_esc(text)}</p>")
        else:
            parts.append(f"<p>{_esc(text)}</p>")

    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph(Paragraph(child, doc))
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            rows: List[str] = []
            for row in table.rows:
                seen, cells = set(), []
                for cell in row.cells:
                    if id(cell._tc) in seen:           # merged cells repeat
                        continue
                    seen.add(id(cell._tc))
                    cells.append(f"<td>{_esc(' '.join(cell.text.split()))}</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            if rows:
                parts.append('<table border="1" cellpadding="3">' + "".join(rows) + "</table>")
                chars += sum(len(r) for r in rows) // 3
    if chars < 20:
        raise DocxError("The Word document has no readable text.")
    return "<html><body>" + "".join(parts) + "</body></html>", notes


def analyze(data: bytes) -> List[str]:
    """Notes about what a conversion of this file leaves out."""
    validate_container(data)
    return _to_html(data)[1]


def convert(data: bytes) -> DocxConversion:
    validate_container(data)
    markup, notes = _to_html(data)
    css = "body{font-family:sans-serif;font-size:10.5pt;line-height:1.35} h1{font-size:15pt} h2{font-size:12pt} p{margin:0 0 6pt 0}"
    story = pymupdf.Story(markup, user_css=css)
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    media = pymupdf.paper_rect("a4")
    where = media + (56, 56, -56, -56)
    more, pages = 1, 0
    while more:
        dev = writer.begin_page(media)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        pages += 1
        if pages > 400:
            raise DocxError("The document is too long to process.")
    writer.close()
    return DocxConversion(pdf=out.getvalue(), page_count=pages, notes=notes)
