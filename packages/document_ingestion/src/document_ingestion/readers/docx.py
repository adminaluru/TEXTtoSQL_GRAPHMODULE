"""Word reader: paragraphs (with their heading) and table rows, in document order.

Hidden text is skipped and counted (D-010). Headers, footers, comments,
text boxes and tracked changes are not read (see README).
"""
import io

from docx import Document
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph

from ..limits import IngestionLimits
from ..models import Block, Location
from ..text import clean_text


def read_docx(data: bytes, limits: IngestionLimits) -> tuple[list[Block], list[str]]:
    document = Document(io.BytesIO(data))
    reader = _Reader()
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            reader.paragraph(item)
        elif isinstance(item, Table):
            reader.table(item)

    notes = []
    if reader.hidden_runs:
        notes.append(f"{reader.hidden_runs} piece(s) of hidden text were skipped.")
    return reader.blocks, notes


class _Reader:
    def __init__(self):
        self.blocks: list[Block] = []
        self.section: str | None = None
        self.paragraph_number = 0
        self.table_number = 0
        self.hidden_runs = 0

    def paragraph(self, paragraph: Paragraph) -> None:
        text = clean_text(self._visible_text(paragraph))
        if not text:
            return
        self.paragraph_number += 1
        if _is_heading(paragraph):
            self.section = text
        location = Location("section", self.paragraph_number, self.paragraph_number, self.section)
        self.blocks.append(Block(text, location))

    def table(self, table: Table) -> None:
        self.table_number += 1
        name = f"table {self.table_number}"
        if self.section:
            name += f" (section “{self.section}”)"
        for number, row in enumerate(table.rows, start=1):
            cells, seen = [], set()
            for cell in row.cells:
                # A merged cell is returned once per grid column it spans.
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                cells.append(clean_text(" ".join(self._visible_text(p) for p in cell.paragraphs)))
            text = " | ".join(cells)
            if text.replace("|", "").strip():
                self.blocks.append(Block(text, Location("table", number, number, name)))

    def _visible_text(self, paragraph: Paragraph) -> str:
        paragraph_hidden = _style_hidden(paragraph.style)
        parts = []
        for item in paragraph.iter_inner_content():
            runs = item.runs if isinstance(item, Hyperlink) else [item]
            for run in runs:
                hidden = run.font.hidden
                if hidden is None:
                    hidden = _style_hidden(run.style) or paragraph_hidden
                if hidden:
                    if run.text.strip():
                        self.hidden_runs += 1
                    continue
                parts.append(run.text)
        return "".join(parts)


def _is_heading(paragraph: Paragraph) -> bool:
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    return name == "Title" or name.startswith("Heading")


def _style_hidden(style) -> bool:
    # A style inherits from its base style unless it sets the value itself.
    while style is not None:
        if style.font.hidden is not None:
            return bool(style.font.hidden)
        style = style.base_style
    return False
