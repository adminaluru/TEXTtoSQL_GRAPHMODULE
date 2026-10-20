"""Keep only sentences that cite shown passages and match what those passages say.

A model can write a fluent sentence that its citation does not support. These
checks are deterministic and cheap; they catch invented numbers, citations to
passages that were never shown, and sentences unrelated to their source. They
are a guard, not a proof of meaning: the passages are always shown with the
answer so the reader can confirm.
"""
import math
import re

from .models import CheckedSentence, DraftSentence, Fact, Passage

MAX_SENTENCES = 4
MAX_SENTENCE_CHARS = 400
# Share of a sentence's content words that must appear in its cited passages.
MIN_WORD_SUPPORT = 1 / 3

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[^\W\d_]{4,}")
_MARKER = re.compile(r"\s*\[(?:\d+(?:\s*,\s*\d+)*|database)\]")
_PLACEHOLDER = re.compile(r"\{([A-Za-z]+\d+)\}")
_STOPWORDS = frozenset("""
    about above after again against also because been before being below between both cannot could does
    doing down during each from further have having here into itself just more most must only other over
    same shall should some such than that their them then there these they this those through under until
    very were what when where which while will with within without would your yours
""".split())


def numbers(text: str) -> set[str]:
    """Numbers as written, without thousands separators: "10,000" and "10000" match."""
    found = set()
    for token in _NUMBER.findall(text):
        found.add(token.replace(",", ""))
        # "1.5" stays "1.5"; a trailing ".00" also matches the whole number.
        if re.fullmatch(r"\d+\.0+", token.replace(",", "")):
            found.add(token.replace(",", "").split(".")[0])
    return found


def content_words(text: str) -> set[str]:
    # A five-letter prefix lets "payments" match "payment" and "approved" match "approval".
    return {w[:5] for w in _WORD.findall(text.casefold()) if w not in _STOPWORDS}


def check_sentence(sentence: DraftSentence, passages: list[Passage],
                   facts: tuple[Fact, ...] = ()) -> CheckedSentence | str:
    """The checked sentence (database figures filled in), or the reason it is left out."""
    text = _MARKER.sub("", sentence.text).strip()
    if not text:
        return "empty sentence"
    if len(text) > MAX_SENTENCE_CHARS:
        return "sentence too long"
    known = {f.id: f for f in facts}
    used = _PLACEHOLDER.findall(text)
    # Without database facts the id list means nothing; with them, every id must be known.
    listed = list(sentence.database_fact_ids) if facts else []
    if any(u not in known for u in used) or any(f not in known for f in listed):
        return "refers to a database figure that was not given"
    cited_facts = tuple(dict.fromkeys(listed + used))
    cited = tuple(dict.fromkeys(sentence.sources))
    if not cited and not cited_facts:
        return "no source cited"
    if any(not 1 <= n <= len(passages) for n in cited):
        return "cites a passage that was not shown"
    bare = _PLACEHOLDER.sub(" ", text)
    if "{" in bare or "}" in bare:
        return "malformed database placeholder"
    # The model never sees database figures, so any number it writes must be
    # copied from a cited passage (or from the wording around a figure, e.g. a year).
    source_text = " ".join([passages[n - 1].text for n in cited]
                           + [_PLACEHOLDER.sub(" ", known[f].statement) for f in cited_facts])
    missing = numbers(bare) - numbers(source_text)
    if missing:
        return f"number not in its sources: {', '.join(sorted(missing))}"
    words = content_words(bare)
    if words:
        shared = words & content_words(source_text)
        if len(shared) < max(1, math.ceil(len(words) * MIN_WORD_SUPPORT)):
            return "wording does not match its sources"
    filled = _PLACEHOLDER.sub(lambda m: known[m.group(1)].value, text)
    return CheckedSentence(text=filled, sources=cited, facts=cited_facts)
