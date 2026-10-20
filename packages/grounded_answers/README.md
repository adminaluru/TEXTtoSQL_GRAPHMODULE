# Grounded answers

Independent package: depends only on `pydantic`. No LLM provider, database or FlowOne application
imports. The drafting model is injected as a function.

Install: python -m pip install -e '.[test]'
Test: python -m pytest tests

## What it does (Step 12a, decision D-012)

1. The question and the retrieved passages go to the model as one JSON value, after fixed
   instructions: answer only from the passages, at most 4 sentences, list the passages behind each
   sentence, copy numbers exactly, say "not answerable" when the passages do not answer. Passages are
   data: instructions inside them are to be ignored.
2. Every sentence the model returns is **checked** (`checker.py`) and left out when:
   - it cites no passage, or a passage that was not shown;
   - it contains a number that is not in the passages it cites (`10,000` and `10000` match);
   - fewer than a third of its content words appear in the passages it cites.
3. The result says what happened:

| status | Meaning | What the application shows |
|--------|---------|----------------------------|
| `written` | At least one sentence passed | The sentences, each followed by its passage numbers, then the passages |
| `not_answered` | The model said the passages do not answer | The closest passages, quoted |
| `unsupported` | Every sentence failed the checks | The passages, quoted |
| `failed` | Model unreachable, too slow, or wrong reply shape | The passages, quoted |

```python
writer = GroundedAnswerWriter(draft=my_model_function)
answer = writer.write("What is the late fee?", [Passage(text="...", source="policy.docx, Fees")])
answer.status    # "written"
answer.text      # "Late payments incur a fee of 2% per month. [1]"
answer.dropped   # reasons for sentences left out
```

## Limits

The checks catch invented numbers, false citations and unrelated sentences. They do not prove that a
sentence means what its passage means (e.g. a negation), so the passages are always shown with the
answer.
