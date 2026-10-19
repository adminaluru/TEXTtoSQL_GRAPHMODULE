"""Size and complexity limits for one uploaded file (decision D-010).

The file size limit is the owner's decision. The others are technical
safety limits that stop a small file from turning into a huge amount of
work (for example a "zip bomb": a tiny Office file that unpacks to gigabytes).
"""
from dataclasses import dataclass

MB = 1024 * 1024


@dataclass(frozen=True)
class IngestionLimits:
    # Owner decision D-010.
    max_file_bytes: int = 25 * MB

    # Word and Excel files are zip archives; these bound what is unpacked.
    max_uncompressed_bytes: int = 100 * MB
    max_compression_ratio: int = 200
    max_archive_entries: int = 5000

    max_pdf_pages: int = 500
    max_sheet_cells: int = 200_000

    # Passages are the searchable units (Step 9 searches them).
    max_passage_chars: int = 1500
    max_passages: int = 10_000
