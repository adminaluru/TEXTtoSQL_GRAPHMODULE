"""Gives the application tests the file builders of the document_ingestion package."""
import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).parents[1] / "packages" / "document_ingestion" / "tests" / "docbuilders.py"


def _load():
    if "docbuilders" in sys.modules:
        return sys.modules["docbuilders"]
    spec = importlib.util.spec_from_file_location("docbuilders", _PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["docbuilders"] = module
    spec.loader.exec_module(module)
    return module


_builders = _load()
make_docx = _builders.make_docx
make_pdf = _builders.make_pdf
make_xlsx = _builders.make_xlsx
