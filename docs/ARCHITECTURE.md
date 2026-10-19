# FlowOne architecture: a guide for new developers

FlowOne answers business questions ("How much has Blue Prism Technologies LE-1001 paid?")
from structured data, safely. The code is split into **layers**. Each layer has one kind of
job, and code may only use layers **below** it. `tests/test_architecture_layers.py` checks
this automatically, so a mistake shows up as a failing test with a hint.

## The layers (top to bottom)

| # | Layer | Folders | Its job | Must not |
|---|-------|---------|---------|----------|
| 1 | **Entry points** | `api/`, `tools/` | Receive HTTP requests or command-line calls, check who the caller is, return responses. | Contain business rules. |
| 2 | **Coordination** | `agent/`, `adapters/` | Run the steps of answering a question in the right order (`orchestrator.py`), build all objects (`factory.py`), and expose the engine through shared contracts (`adapters/`). | Talk to HTTP. |
| 3 | **Business rules** | `nlu/`, `semantic/`, `planning/`, `sql_compiler/`, `sql_validator/`, `sql_guardrails/`, `result_analysis/`, `answering/` | Understand the question, plan the query, build safe SQL, check it, and word the answer. Deterministic and testable without databases. | Read configuration or call HTTP. |
| 4 | **Infrastructure** | `ai/`, `database/`, `execution/`, `ontology/` | Talk to the outside world: the language model, Neo4j (business metadata), PostgreSQL (read-only queries). | Make business decisions. |
| 5 | **Shared basics** | `observability/`, `config.py` | Timing/logging helpers and settings read from the environment. | Depend on anything above. |
| — | **Portable packages** | `packages/*` | Pieces any application can reuse (see below). | Import **any** FlowOne folder. |

Known exception (kept visible in the test): `execution/` receives the compiled, validated and
approved query objects from `sql_compiler/`, `sql_validator/` and `sql_guardrails/`.

## Portable packages

Each is installable on its own (`pip install -e packages/<name>`) and has its own tests and
README. They are the building blocks other systems (for example an existing RAG application)
can use without the rest of FlowOne.

| Package | One job |
|---------|---------|
| `hybrid_contracts` | The shared language: requests, results, evidence, tables, clarifications, errors. |
| `trusted_identity` | Who is calling and for which company: verifies signed tokens (development secret now, Microsoft Entra ID later by configuration). |
| `tenant_scope` | Which rows a company may see: maps a logical tenant to its company and returns row filters. |
| `clarification_state` | Remembers "which of these did you mean?" choices safely: per user, tenant and conversation, expiring, single use. |
| `question_router` | Decides which sources answer a question: fixed rules first, one AI classifier call only when unsure, both sources when still unclear (D-011). |
| `document_retrieval` | Finds passages for a question in the caller's workspace and returns them quoted, with file and place (page, section, sheet rows). Same `Retriever` contract as the SQL engine. |
| `document_ingestion` | Turns an uploaded Word, PDF or Excel file into searchable passages with their location (page, section, sheet row), and runs the document lifecycle (upload, processing, replacement, deletion). Storage is plugged in through small interfaces (`ports.py`). |

## The web UI

`ui/` is a separate React + TypeScript project (see `ui/README.md`). It sits **outside** the
Python layers and depends only on the public HTTP API, with the same idea of layers inside it:
`src/api/` (HTTP) → `src/state/` (what the chat remembers) → `src/components/` (screens).
It never imports backend code and never decides permissions.

## The life of one uploaded document

```
POST /documents  (Authorization: Bearer <token>, multipart field "file")
 1  api/document_routes.py     token -> verified caller; size checked before reading
 2  document_ingestion          content check (type, size, macros, zip bombs) -> 202 queued
       service.upload           file saved under the caller's workspace (adapters/document_files.py)
 3  adapters/document_worker.py background thread picks up the job
 4  adapters/isolated_extractor.py   reads the file in a separate process with a time limit
       readers/                 text + location; hidden content skipped and reported
       chunking.py              passages of at most 1,500 characters
 5  adapters/document_sqlite.py passages saved in the workspace's search index -> ready
GET /documents                  status of each document (queued, processing, ready, failed)
DELETE /documents/{id}          passages removed first, then the files
POST /documents/search         document_retrieval: search words -> workspace-filtered full-text
                                search (adapters/document_search.py) -> each hit checked again
                                -> passages quoted exactly, with citations
```

Document text is data. Nothing in a file can change who owns it or what FlowOne does.

## Which sources answer a question (Step 11)

```
POST /ask -> agent/hybrid.py
   question_router: rules (no AI) -> unsure? one AI classifier call -> still unclear? both
   database only   -> the research agent below, unchanged
   documents only  -> document_retrieval: quoted passages with citations
   both            -> both in parallel, one deadline; a failed part is reported, never hidden
```

## The life of one question

```
POST /ask  (Authorization: Bearer <token>)
 1  api/identity.py            token -> verified caller (trusted_identity)
 2  api/application.py         builds the agent for this caller (tenant scope, choices)
 3  agent/orchestrator.py      runs the steps below, in order
 4    nlu/                     language model -> structured question (only LLM step)
 5    semantic/                checks and normalises the business meaning
 6    planning/                finds tables and joins in the ontology, resolves columns,
                               turns dates into exact periods, splits money by currency,
                               keeps empty groups, notes missing values
 7    planning/*_guard.py      stops unsafe or ambiguous plans (fan-out, unknown values,
                               ambiguous names -> clarification)
 8    sql_compiler/            plan -> parameterised SQL (values are never pasted into SQL)
 9    sql_validator/ sql_guardrails/   checks the SQL is read-only and within limits
10    execution/               runs it read-only with a timeout
11    result_analysis/ answering/      turns rows into a factual answer
```

Every number in an answer comes from the database; the language model only interprets the
question.

The whole question shares one time budget (`observability/deadline.py`, default 60 s): every
language-model call, Neo4j query and PostgreSQL statement gets at most the time that is left,
and PostgreSQL cancels a statement that would run past it.

## Where do I add...?

| You want to... | Put it in |
|----------------|-----------|
| a new HTTP endpoint | `api/application.py` (HTTP only), calling the agent |
| a new rule about how questions become queries | `planning/` (a small class with its own test file) |
| a new storage for documents (e.g. cloud) | a new adapter implementing a port in `packages/document_ingestion/.../ports.py`, chosen in `api/document_setup.py` |
| a new data source | a new retriever implementing `hybrid_contracts.Retriever`, wired in `agent/factory.py` or `api/runtime.py` |
| a new setting | `config.py`, then pass the value in from `api/runtime.py` or `agent/factory.py` |
| something other applications should reuse | a new folder under `packages/` with `pyproject.toml`, `README.md` and `tests/` |

## Rules of thumb

1. **Pass dependencies in; don't reach out for them.** Classes receive what they need through
   their constructor (`EntityMatchGuard(ontology, compiler, ...)`), so tests can pass fakes.
2. **Refuse rather than guess.** When something is unclear (two customers with the same name,
   an unknown status, "last week"), the agent asks or says it cannot answer. It never picks one.
3. **Identity comes only from the verified token**, never from the question or request body.
4. **One file, one job.** If a file starts doing two things, split it.

Decisions behind these rules are recorded in `docs/project/DECISION_LOG.md`.
