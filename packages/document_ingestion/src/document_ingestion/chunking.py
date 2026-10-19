"""Group neighbouring blocks into passages of bounded size.

A passage never mixes two places: a new section, table or sheet always
starts a new passage, and each PDF page is its own passage (or several,
when long), so every passage can be cited by one clear location.
"""
from .models import Block, Location, Passage


def split_into_passages(blocks: list[Block], max_chars: int) -> list[Passage]:
    passages: list[Passage] = []
    texts: list[str] = []
    location: Location | None = None

    def flush():
        nonlocal texts, location
        if texts and location is not None:
            passages.append(Passage(len(passages) + 1, "\n".join(texts), location))
        texts, location = [], None

    for block in blocks:
        for piece in _pieces(block.text, max_chars):
            joined = sum(len(t) + 1 for t in texts) + len(piece)
            if location is not None and _continues(location, block.location) and joined <= max_chars:
                texts.append(piece)
                location = Location(location.kind, location.start, block.location.end, location.name)
            else:
                flush()
                texts, location = [piece], block.location
    flush()
    return passages


def _continues(current: Location, following: Location) -> bool:
    if current.kind == "page":
        return False
    return (current.kind, current.name) == (following.kind, following.name) \
        and following.start in (current.end, current.end + 1)


def _pieces(text: str, max_chars: int) -> list[str]:
    """Split one long block at whitespace, never mid-word unless unavoidable."""
    pieces = []
    while len(text) > max_chars:
        cut = text.rfind("\n", 0, max_chars + 1)
        if cut <= 0:
            cut = text.rfind(" ", 0, max_chars + 1)
        if cut <= 0:
            cut = max_chars
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces
