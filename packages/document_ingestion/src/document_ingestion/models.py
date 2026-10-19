"""What extraction produces: text together with the place it came from."""
from dataclasses import dataclass
from typing import Literal

FileType = Literal["pdf", "docx", "xlsx"]


@dataclass(frozen=True)
class Location:
    """
    Where a piece of text is in the file, readable by a person.

    kind     page    -> PDF page numbers
             section -> Word paragraph numbers, under the heading `name`
             table   -> Word table rows; `name` is e.g. "table 2"
             sheet   -> Excel row numbers; `name` is the sheet name
    """

    kind: Literal["page", "section", "table", "sheet"]
    start: int
    end: int
    name: str | None = None

    @property
    def label(self) -> str:
        span = str(self.start) if self.start == self.end else f"{self.start}–{self.end}"
        plural = "" if self.start == self.end else "s"
        if self.kind == "page":
            return f"page{plural} {span}"
        if self.kind == "section":
            where = f"section “{self.name}”, " if self.name else ""
            return f"{where}paragraph{plural} {span}"
        if self.kind == "table":
            return f"{self.name}, row{plural} {span}"
        return f"sheet “{self.name}”, row{plural} {span}"


@dataclass(frozen=True)
class Block:
    """One paragraph, table row, sheet row or PDF page of text."""

    text: str
    location: Location


@dataclass(frozen=True)
class Passage:
    """A searchable piece of the document: neighbouring blocks, bounded in size."""

    ordinal: int
    text: str
    location: Location


@dataclass(frozen=True)
class ExtractedDocument:
    file_type: FileType
    sha256: str
    size_bytes: int
    passages: tuple[Passage, ...]
    # What was deliberately not read, e.g. "2 hidden sheet(s) were skipped."
    notes: tuple[str, ...] = ()
