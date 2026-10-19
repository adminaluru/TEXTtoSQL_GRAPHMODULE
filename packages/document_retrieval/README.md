# Document retrieval

Independent package: depends only on `flowone-hybrid-contracts`. No FastAPI, database, search
engine, LLM or FlowOne application imports.

Install: python -m pip install -e '.[test]'
Test: python -m pytest tests

## What it does

`DocumentRetriever` answers a `RetrievalRequest` (route `document_rag`) with quoted passages from
the caller's workspace, each with a citation:

```
"Late payment fee is 2% per month."      terms.pdf · page 3      (document doc_…, version 2)
```

1. **Search words** (`terms.py`): letters and digits only, filler words dropped, at most 12.
   Nothing typed can change the search syntax.
2. **Search** (`PassageSearch` port): best matches within the tenant.
3. **Check again** (`DocumentDirectory` port): each hit must belong to the tenant, to a document that
   is not deleted, and to the version in use. Anything else is dropped.
4. **Evidence**: the passage text exactly as stored, with document, version and location.

| Situation | Status |
|-----------|--------|
| Passages found | `complete` |
| Nothing matches | `empty`, with "No passage … matches these words." |
| No searchable words ("what is the?") | `clarification` |
| Caller not permitted | `denied` |
| Storage down | `unavailable` |
| Deadline passed, unexpected error | `failed` (safe message; details logged) |

## What it does not do

- It does not verify identity: `request.access` must come from verified identity.
- It does not summarise or reword. Writing an answer from passages is Steps 11–12; passages are
  untrusted text and must be handed to a language model as data, never as instructions.
- Ranking is the search backend's (SQLite FTS5 BM25 today). Reranking is added only if
  evaluations show it is needed.
