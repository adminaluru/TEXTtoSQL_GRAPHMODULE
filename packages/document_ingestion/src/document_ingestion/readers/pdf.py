"""PDF reader: the text of each page. Scanned pages have no text (D-010)."""
import io

from pypdf import PasswordType, PdfReader

from ..errors import DocumentRejected
from ..limits import IngestionLimits
from ..models import Block, Location
from ..text import clean_text

SCANNED = ("This PDF has no readable text (it may be a scanned image). "
           "Scanned PDFs are not supported yet.")


def read_pdf(data: bytes, limits: IngestionLimits) -> tuple[list[Block], list[str]]:
    reader = PdfReader(io.BytesIO(data))

    if reader.is_encrypted:
        # Many PDFs are "encrypted" with an empty open password (only printing
        # or copying is restricted); those are readable. A real password is not.
        if reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise DocumentRejected("encrypted", "The PDF is password-protected. Remove the password and upload it again.")

    if len(reader.pages) > limits.max_pdf_pages:
        raise DocumentRejected("too_complex", f"The PDF has more than {limits.max_pdf_pages} pages.")

    blocks: list[Block] = []
    pages_without_text = 0
    for number, page in enumerate(reader.pages, start=1):
        text = clean_text(page.extract_text() or "")
        if text:
            blocks.append(Block(text, Location("page", number, number)))
        else:
            pages_without_text += 1

    if not blocks:
        raise DocumentRejected("no_readable_text", SCANNED)

    notes = []
    if pages_without_text:
        notes.append(f"{pages_without_text} page(s) without readable text were skipped (possibly scanned).")
    # Embedded files and scripts are never opened or run.
    if reader.attachments:
        notes.append(f"{len(reader.attachments)} embedded file(s) were not read.")
    return blocks, notes
