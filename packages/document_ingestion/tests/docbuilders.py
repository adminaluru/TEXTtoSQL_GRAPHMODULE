"""Build small Word, Excel and PDF files in memory for the tests.

Every file is generated, so the tests need no binary fixtures and show
exactly what each file contains.
"""
import io
import re
import zipfile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def make_docx(build) -> bytes:
    """`build(document)` adds content with the python-docx API."""
    document = Document()
    build(document)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def add_hyperlink(paragraph, text: str, url: str) -> None:
    rel_id = paragraph.part.relate_to(
        url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    text_element = OxmlElement("w:t")
    text_element.text = text
    run.append(text_element)
    link.append(run)
    paragraph._p.append(link)


def make_xlsx(build, saved_values: dict[tuple[str, str], str] | None = None) -> bytes:
    """
    `build(workbook)` adds content with openpyxl.

    openpyxl never stores calculated formula results, but Excel does.
    `saved_values` {(sheet file, cell): value} writes the result Excel would
    have saved, e.g. {("sheet1", "C4"): "500"}.
    """
    workbook = Workbook()
    build(workbook)
    out = io.BytesIO()
    workbook.save(out)
    data = out.getvalue()
    for (sheet, cell), value in (saved_values or {}).items():
        data = _replace_entry(
            data, f"xl/worksheets/{sheet}.xml",
            lambda xml, cell=cell, value=value: re.sub(
                rf'(<c r="{cell}"[^>]*><f>.*?</f>)<v\s*/?>(</v>)?', rf"\g<1><v>{value}</v>", xml))
    return data


def make_pdf(pages: list[str | None], password: str | None = None, owner_only: bool = False) -> bytes:
    """
    One PDF page per item: text, or None for a page with no text (like a scan).

    password: open password. owner_only=True sets only a permissions
    password, so the file opens without one (common for "protected" PDFs).
    """
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for text in pages:
        page = writer.add_blank_page(612, 792)
        if text is None:
            continue
        lines = b" ".join(b"(" + _pdf_escape(line) + b") Tj T*" for line in text.split("\n"))
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 14 TL 72 720 Td " + lines + b" ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    if password is not None:
        if owner_only:
            writer.encrypt(user_password="", owner_password=password, algorithm="AES-128")
        else:
            writer.encrypt(user_password=password, owner_password=password, algorithm="AES-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def make_zip(entries: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return out.getvalue()


def add_entry(data: bytes, name: str, content: bytes) -> bytes:
    """Copy of an Office file with one extra part (e.g. a macro project)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr(name, content)
    return out.getvalue()


def _replace_entry(data: bytes, name: str, change) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename == name:
                content = change(content.decode("utf-8")).encode("utf-8")
            target.writestr(item, content)
    return out.getvalue()


def _pdf_escape(text: str) -> bytes:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")
