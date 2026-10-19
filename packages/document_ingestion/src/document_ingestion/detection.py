"""Decide what a file really is, from its content, before any reader opens it.

The file name is only a hint: a renamed file is refused when its content
does not match its extension.
"""
import io
import zipfile

from defusedxml.ElementTree import fromstring

from .errors import DAMAGED, DocumentRejected
from .limits import IngestionLimits
from .models import FileType

SUPPORTED = "Only Word (.docx), PDF (.pdf) and Excel (.xlsx) files are supported."
MACROS = "Files with macros are not accepted. Save the file as .docx or .xlsx without macros."

# Old .doc/.xls files, and password-protected Office files, use this container.
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP = b"PK\x03\x04"
_MACRO_EXTENSIONS = {"docm", "dotm", "xlsm", "xltm", "xlsb", "xlam"}


def detect_file_type(data: bytes, filename: str, limits: IngestionLimits) -> FileType:
    if not data:
        raise DocumentRejected("empty", "The file is empty.")
    if len(data) > limits.max_file_bytes:
        megabytes = limits.max_file_bytes // (1024 * 1024)
        raise DocumentRejected("too_large", f"The file is larger than {megabytes} MB.")

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in _MACRO_EXTENSIONS:
        raise DocumentRejected("macros", MACROS)
    if extension not in ("pdf", "docx", "xlsx"):
        raise DocumentRejected("unsupported_type", SUPPORTED)

    if data.startswith(b"%PDF-"):
        actual: FileType = "pdf"
    elif data.startswith(_OLE):
        raise DocumentRejected(
            "encrypted",
            "The file is password-protected or in an old Office format (.doc/.xls). "
            "Remove the password or save it as .docx/.xlsx.")
    elif data.startswith(_ZIP):
        actual = _office_type(data, limits)
    else:
        raise DocumentRejected("unsupported_type", SUPPORTED)

    if actual != extension:
        raise DocumentRejected("type_mismatch", f"The file content is not a .{extension} file. {SUPPORTED}")
    return actual


def _office_type(data: bytes, limits: IngestionLimits) -> FileType:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocumentRejected("malformed", DAMAGED) from exc

    with archive:
        entries = archive.infolist()
        if len(entries) > limits.max_archive_entries:
            raise DocumentRejected("too_complex", "The file has too many internal parts to process.")
        # Sizes come from the archive's own headers; Python stops reading at
        # the declared size, so a lying header makes the read fail, not grow.
        unpacked = sum(e.file_size for e in entries)
        packed = max(1, sum(e.compress_size for e in entries))
        if unpacked > limits.max_uncompressed_bytes or unpacked / packed > limits.max_compression_ratio:
            raise DocumentRejected("too_complex", "The file expands to too much data to process.")

        names = {e.filename.lower() for e in entries}
        if any(name.endswith("vbaproject.bin") for name in names):
            raise DocumentRejected("macros", MACROS)
        if any("macroenabled" in t.lower() for t in _content_types(archive)):
            raise DocumentRejected("macros", MACROS)

        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
    raise DocumentRejected("unsupported_type", SUPPORTED)


def _content_types(archive: zipfile.ZipFile) -> list[str]:
    try:
        info = archive.getinfo("[Content_Types].xml")
    except KeyError as exc:
        raise DocumentRejected("unsupported_type", SUPPORTED) from exc
    if info.file_size > 1024 * 1024:
        raise DocumentRejected("too_complex", "The file has an unusually large type list.")
    try:
        # defusedxml refuses entity expansion and external references.
        root = fromstring(archive.read(info))
    except Exception as exc:
        raise DocumentRejected("malformed", DAMAGED) from exc
    return [element.get("ContentType", "") for element in root]
