"""Turn a question into search words.

Only letters and digits survive, so nothing a user types can change the
search syntax. Common words ("what", "the", "is") carry no meaning for a
document search and are dropped.
"""
import re

MAX_TERMS = 12

_WORD = re.compile(r"[^\W_]+", re.UNICODE)

STOPWORDS = frozenset("""
a about above after again all am an and any are as at be been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers
him his how i if in into is it its itself just me more most my no nor not now of off on once only or
other our ours out over own same she should so some such than that the their theirs them then there
these they this those through to too under until up very was we were what when where which while who
whom why will with would you your yours
tell show find give list explain say says said please document documents file files
""".split())


def search_terms(question: str) -> list[str]:
    seen: dict[str, None] = {}
    for word in _WORD.findall(question.casefold()):
        if word in STOPWORDS:
            continue
        if len(word) < 2 and not word.isdigit():
            continue
        seen.setdefault(word, None)
        if len(seen) == MAX_TERMS:
            break
    return list(seen)
