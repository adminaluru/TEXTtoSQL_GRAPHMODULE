"""Step 12.0 (D-012): the document answer evaluation set, checked without any AI call.

The written answers are measured live with tools/run_answer_eval.py --live.
Here: the file is well formed, and search finds the passage each answerable
question should cite, so a wrong live answer is the writer's, not the search's.
"""
import pytest

from tools.run_answer_eval import PASSAGE_LIMIT, contains, evaluate, load

DATA = load()


def test_evaluation_file_is_well_formed():
    ids = [q["id"] for q in DATA["questions"]]
    assert len(ids) == len(set(ids)) >= 15
    for q in DATA["questions"]:
        assert set(q) == {"id", "question", "answerable", "must_include", "must_exclude", "cite"}
        assert (q["cite"] is not None) == q["answerable"]
        assert q["must_include"] or not q["answerable"]
    assert any(not q["answerable"] for q in DATA["questions"])
    assert any("instruction" in text.casefold()
               for d in DATA["documents"] for _, text in d.get("sections", ())), "planted instruction missing"


def test_search_finds_the_passage_every_answerable_question_should_cite():
    rows = evaluate(DATA)
    missed = [r["id"] for r in rows if r["answerable"] and not r["expected_passage_found"]]
    assert not missed, f"expected passage not in the top {PASSAGE_LIMIT}: {missed}"


@pytest.mark.parametrize("text,phrase,expected", [
    ("Write-offs above 10,000 USD", "10000", True),
    ("approved by the Credit Manager", "credit manager", True),
    ("2% per month", "3%", False),
])
def test_phrase_matching(text, phrase, expected):
    assert contains(text, phrase) is expected
