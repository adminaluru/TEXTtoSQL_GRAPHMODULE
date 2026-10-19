from .docx import read_docx
from .pdf import read_pdf
from .xlsx import read_xlsx

READERS = {"pdf": read_pdf, "docx": read_docx, "xlsx": read_xlsx}

__all__ = ["READERS", "read_docx", "read_pdf", "read_xlsx"]
