# Component dependency map

Arrows mean "imports". Portable packages must not import FlowOne application modules.

| Component | Kind | May depend on | Must not depend on | Check |
|-----------|------|---------------|--------------------|-------|
| `packages/hybrid_contracts` | Portable contracts | pydantic | app modules, FastAPI, drivers | own tests |
| `packages/tenant_scope` | Portable policy | pydantic, `hybrid_contracts` (public API only) | app modules, drivers | AST import test; isolated `python -I` run |
| `packages/trusted_identity` | Portable identity | pydantic, PyJWT[crypto], `hybrid_contracts` | app modules, web frameworks | AST import test; isolated run |
| `packages/clarification_state` | Portable state | `hybrid_contracts` (public API), stdlib | app modules, drivers | AST import test; isolated `python -I` run |
| `packages/document_ingestion` | Portable extraction (D-010) | python-docx, pypdf, openpyxl, defusedxml, stdlib | app modules, web frameworks, storage, LLM | AST import test; isolated `python -I` run |
| `packages/document_retrieval` | Portable retrieval (Step 9) | `hybrid_contracts` | app modules, search engines, LLM | AST import test; isolated `python -I` run |
| `adapters/document_search.py` | Adapter: retrieval ports on SQLite FTS5 | `document_retrieval`, `document_ingestion` (Location), `adapters.document_sqlite` | FastAPI, config | `tests/test_document_search.py` |
| `packages/question_router` | Portable routing rules and plan (Step 11) | `hybrid_contracts` | app modules, LLM, databases | AST import test; isolated `python -I` run |
| `agent/hybrid.py` | Coordinator: plan -> SQL agent and/or document retriever | `question_router`, `document_retrieval` (via injection), `answering`, `observability` | FastAPI | `tests/test_hybrid_assistant.py` |
| `nlu/route_classifier.py` | AI classifier for unsure questions | `ai.interfaces`, `hybrid_contracts` | config, databases | `tests/test_hybrid_assistant.py` |
| `planning/entity_match_guard.py` | Domain (app) | `planning.models`; injected ontology/compiler/validator/guardrails/executor/scope policy | FastAPI, LLM providers | unit tests with doubles |
| `planning/tenant_entity_scope.py` | Adapter | `tenant_scope`, `hybrid_contracts`, `planning.models` | — | `tests/test_tenant_entity_scope.py` |
| `planning/entity_choice_labels.py` + `.json` | Reviewed label data (D-003) | `planning.entity_match_guard` | — | loaded in live tests |
| `planning/currency_split.py`, `empty_groups.py`, `missing_values.py` | Plan policies (D-004–D-006) | `planning.models`, injected ontology; reviewed data `measure_currency.json` | FastAPI, drivers | unit tests with doubles + live |
| `semantic/existence.py` | Shared rule | — | — | unit tests |
| `agent/orchestrator.py` | Coordinator | `clarification_state`, `hybrid_contracts`, guard | FastAPI | unit + live flow tests |
| `adapters/sql_retriever.py` | Adapter: engine -> `Retriever` contract | `hybrid_contracts`, `clarification_state`, injected `agent_for(access)` | FastAPI | `tests/test_sql_retriever.py` |
| `tests/rag_host_stub.py` | Representative host | `hybrid_contracts` only (AST-checked) | everything else | `tests/test_sql_retriever.py` |
| `adapters/document_files.py`, `document_sqlite.py`, `document_worker.py`, `isolated_extractor.py` | Adapters: ports of `document_ingestion` | `document_ingestion`, `hybrid_contracts`, stdlib (sqlite3, multiprocessing) | FastAPI, config | `tests/test_document_adapters.py` |
| `api/document_routes.py`, `api/document_setup.py` | HTTP + composition for documents | `document_ingestion`, adapters, `api.identity` | — | `tests/test_document_api.py` |
| `agent/factory.py` | Composition root | everything above | — | e2e tests |

Gaps: the SQL engine (planning, compiler, orchestrator) is application code. SQLRetriever makes it
usable through the portable contract, but the engine itself is not a portable package; extracting it
is only worth doing when another host needs the engine in-process rather than through the contract.

Layer rules for the application folders are described in `docs/ARCHITECTURE.md` and enforced by `tests/test_architecture_layers.py`.
