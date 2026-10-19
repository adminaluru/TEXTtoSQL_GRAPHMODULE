# Question router

Independent package: depends only on `flowone-hybrid-contracts`. No LLM, database or FlowOne
application imports. The classifier is injected; the business record words are passed in.

Install: python -m pip install -e '.[test]'
Test: python -m pytest tests

## How a route is chosen (decision D-011)

| Step | What happens | AI calls |
|------|--------------|----------|
| 1. Rules (`rules.py`) | A measure or listing about a record word -> database; a document word or phrase -> documents; a measure plus a document word -> both | 0 |
| 2. Classifier | Only when the rules are unsure ("What is the late payment fee?") | 1 |
| 3. Still unclear | No classifier, it failed, or it said "unclear" -> both database and documents | — |
| 4. Availability | Documents removed (with a note) when the workspace has none ready; graph until Step 10 | — |

```python
router = QuestionRouter(RouteVocabulary(record_words=frozenset({"invoice", "payment"})), classifier)
plan = router.plan("How many open invoices are there?", available={Route.SQL, Route.DOCUMENT})
plan.routes            # (Route.SQL,)
plan.method            # "rules"
plan.classifier_calls  # 0
```

## Rules this package keeps

- Simple numeric questions never call the classifier and never search documents.
- A route is never added because another failed: an unanswerable database question is not turned
  into a document search.
- The plan lists only routes that are available; the caller runs independent steps in parallel.
