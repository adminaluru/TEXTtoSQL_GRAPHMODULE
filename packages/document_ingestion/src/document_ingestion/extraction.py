"""One entry point: file bytes in, located passages out (or a safe refusal).

Document text is untrusted data. Nothing here interprets it: an
instruction written inside a file is extracted as plain text like any
other sentence and cannot change who owns the document or what is done
with it.
"""
import hashlib
import logging

from .chunking import split_into_passages
from .detection import detect_file_type
from .errors import DAMAGED, DocumentRejected
from .limits import IngestionLimits
from .models import ExtractedDocument
from .readers import READERS

logger = logging.getLogger(__name__)


def extract_document(data: bytes, filename: str,
                     limits: IngestionLimits = IngestionLimits()) -> ExtractedDocument:
    file_type = detect_file_type(data, filename, limits)
    try:
        blocks, notes = READERS[file_type](data, limits)
    except DocumentRejected:
        raise
    except Exception as exc:
        # Reader libraries raise many error types for damaged files; the
        # uploader gets one safe message, the log keeps the type only.
        logger.info("document_unreadable file_type=%s error=%s", file_type, type(exc).__name__)
        raise DocumentRejected("malformed", DAMAGED) from exc

    if not blocks:
        raise DocumentRejected("nothing_to_index", "The file contains no readable text.")

    passages = split_into_passages(blocks, limits.max_passage_chars)
    if len(passages) > limits.max_passages:
        raise DocumentRejected("too_complex", "The file has too much text to index.")

    return ExtractedDocument(
        file_type=file_type,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        passages=tuple(passages),
        notes=tuple(notes),
    )
