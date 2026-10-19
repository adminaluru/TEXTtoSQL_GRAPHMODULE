import re
import unicodedata

# Control and invisible direction characters other than tab and newline:
# they can hide or reorder text without being visible.
_INVISIBLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean_text(value: str) -> str:
    """Normalise extracted text. It stays data: nothing in it is interpreted."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _INVISIBLE.sub("", unicodedata.normalize("NFC", value))
    lines = [_SPACES.sub(" ", line).strip() for line in value.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
