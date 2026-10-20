# Decision log

| ID | Decision | Status | Owner confirmation | Affected tests / code |
|----|----------|--------|--------------------|-----------------------|
| D-001 | **Customer scope.** A customer name in a question means one customer. Resolution happens inside the caller's active company, after retrieval has been restricted to permitted data; a name that matches more than one record inside that company returns a clarification. Cross-company totals are never produced for a customer name. Parent/group aggregation is not supported (no verified hierarchy data). | Decided | Owner, 2026-10-14 | `planning/entity_match_guard.py` (`EntityScopePolicy`), `agent/factory.py` (`entity_scope_policy`), `tests/test_entity_match_regressions.py`, GQ001/002/003/005 tests |
| D-002 | **Tenant mapping (POC).** One company per tenant, as an explicit POC rule. The tenant is a logical identifier (`AccessContext.tenant_id`); the tenant → owning company key and each table's ownership column are separate, explicit mapping data. Never derived from names or ID formats. Unknown tenant or unmapped table fails closed. | Decided (POC rule) | Owner, 2026-10-14 | `packages/tenant_scope`, `planning/tenant_entity_scope.py`, `tests/test_tenant_entity_scope.py` |
| D-003 | **Clarification choice labels.** Each choice for an ambiguous record shows its name and its legal entity name (e.g. "Blue Prism Technologies LE-1001 — Acme Technologies - Primary Legal Entity"). Internal record IDs are never shown; they stay server-side behind opaque option IDs. | Decided | Owner, 2026-10-15 | `planning/entity_choice_labels.json`, `planning/entity_match_guard.py`, `tests/test_entity_clarification_flow.py` |
| D-004 | **Currency.** Amounts are never combined across currencies; SUM/AVG/MIN/MAX of financial measures are reported per currency. A row's own currency column is authoritative (invoices, adjustments); payments and claims take the currency of their linked invoice. | Decided | Owner, 2026-10-15 | `planning/measure_currency.json`, `planning/currency_split.py`, `tests/test_currency_split_regressions.py`; answer text now ends with the currency (GQ001/GQ007 and payment tests updated) |
| D-005 | **Empty groups.** Grouped totals list every grouped record; SUM and COUNT of no records are 0. (Implementation detail, not a new rule: AVG/MIN/MAX of no records have no value and stay empty, never 0.) | Decided | Owner, 2026-10-15 | `planning/empty_groups.py`, `tests/test_empty_group_regressions.py`; GQ004 now 21 rows (3 at 0) |
| D-006 | **Missing values.** Totals state how many measured records have no value (e.g. pending approved amounts) instead of silently ignoring them. | Decided | Owner, 2026-10-15 | `planning/missing_values.py`, `tests/test_missing_value_regressions.py` |
| D-007 | **Dates.** Business time zone is US Central (America/Chicago, daylight-saving aware). Stored timestamps are already Central time (no conversion). A stated end date is included in full (start <= value < day after end). Unclear periods (month without year, weeks, rolling "last N days") are refused, not guessed. | Decided | Owner, 2026-10-15 ("Not sure" was not chosen: storage zone confirmed as Central) | `planning/date_period.py`, `planning/column_resolver.py`, `tests/test_date_period_regressions.py` |
| D-008 | **Identity provider.** Development uses HS256 tokens signed with a shared secret and issued locally; production will use an identity provider (Microsoft Entra ID or any OIDC provider) through configuration only (issuer, audience, JWKS, claim names). Production refuses the shared API key and shared secrets. The active tenant is chosen only from verified memberships (automatic when there is one). | Decided | Owner, 2026-10-15 | `packages/trusted_identity`, `api/identity*.py`, `config.py`, `tests/test_api_identity.py` |
| A-OWN-1 | **Assumption (not yet a decision): linked ownership.** Tables without their own company column belong to the company of the invoice they reference (14 tables: claim invoices and items, promise-to-pay invoices, adjustment items, invoice tags and others, each via one confirmed foreign key to `public.erp_invoice_details`). Five tables have no known owner and are refused. | **Owner to confirm** | Applied in POC so no table is read unrestricted | `docs/examples/tenant_scope.dev.json`, `planning/tenant_plan_scope.py`, `tests/test_tenant_plan_scope.py` |
| D-009 | **UI timing and technology.** Chat UI Phase 1 is pulled forward ahead of Step 8 (SQL answers only); Phase 2 adds documents after Step 9. React + Vite + TypeScript in a separate `ui/` project that uses only the public HTTP API. The owner's mock-up is the target layout, adjusted per `ROADMAP.md` (clarification first, currency codes, no undefined "disputed" status, no placeholder document content). Step 6 stays postponed until before real users. | Decided | Owner, 2026-10-15 | `ui/`, `docs/project/ROADMAP.md` |
| D-010 | **Document ingestion rules (Step 8).** Word (.docx), PDF and Excel (.xlsx) only, up to **25 MB** per file. Scanned PDFs (no readable text) are **refused** for now with a clear message; OCR can be added later as a separate component. Excel: the **value Excel last saved** is used, the formula text is kept beside it as evidence; FlowOne never recalculates. **Hidden content** (hidden sheets, rows, columns, hidden Word text) is **skipped and reported** on the document record. Files are stored in a **local folder outside Git**, one subfolder per workspace, behind a swappable storage adapter. Every document belongs to the workspace it was uploaded in. | Decided | Owner, 2026-10-15 | `packages/document_ingestion`, document adapters, `api/` document routes |
| D-011 | **Routing (Step 11).** Fixed rules decide clear questions with no AI call (a measure or listing about a business record -> database; a document word or phrase -> documents; both kinds -> both). Only when the rules are unsure is one small AI classifier call made. When it is still unclear, FlowOne searches **both** the database and the documents. No response-time target yet: measure first with the routing benchmark, then set one. No fallback that changes the question. | Decided | Owner, 2026-10-16 | `packages/question_router`, `agent/hybrid.py`, `nlu/route_classifier.py`, `agent/route_vocabulary.json`, `docs/benchmarks/routing_questions.v1.json` |
| D-012 | **Grounded answers and saved conversations (Step 12).** Written answers are generated from retrieved passages and database results; **document passages may be sent to the AI provider for synthetic development data only** (revisit before real customer documents). The AI never writes a number: database figures are inserted by the application, other numbers must appear word for word in the cited passage. Every sentence must cite included evidence and is checked; unsupported sentences are dropped, and if nothing survives the quoted passages are shown as before. Passage text is untrusted and cannot change instructions. Conversations are saved on the server per user and company, **kept until the user deletes them**. Follow-up questions that rely on earlier messages are a **later step**; each question is answered on its own. Order: evaluation set (12.0), document answers (12a), combined answers (12b), saved conversations (12c), UI (12d), benchmark (12e). | Decided | Owner, 2026-10-19 | Step 12 (in progress) |

## D-009 amendment (owner, 2026-10-16)

- The Documents panel (upload, status, retry, delete) is pulled forward from Phase 2 so Step 8 can
  be used in the UI now. Document evidence and quotes in answers stay in Phase 2, after Step 9.

## D-001 rationale and impact

- Evidence: see `TEST_EVIDENCE.md` → "Blue Prism entity scope".
- The previous expected values (24000.00 paid, 24 invoices, 4500.00 claims) were the sum
  of six customers across three companies. They were retired, not adjusted.
- Golden numeric tests now pin `Blue Prism Technologies LE-1001`
  (`SYN-CUST-1001-01#527#1001`), whose values were verified with independent read-only SQL.
- The bare name "Blue Prism" is now tested to return a clarification.
- Compatibility: `build_research_agent(..., entity_scope_policy=None)` is optional; with no
  policy the guard behaves as before (unscoped probe). The default path therefore does **not**
  yet enforce the active company — that requires trusted identity (Step 5) and an approved
  source for the mapping data.

## D-002 rationale and impact

- Simple now: one owner key per tenant, one ownership column per table, equality predicate.
- Controlled growth: tenant identity is never the physical key, so moving to several companies
  per tenant (or a different owner column) changes mapping data and `TenantOwnership.owner_key`,
  not callers. That change is a new decision and a contract version bump.
- Not decided: where the production mapping data lives (identity provider claims, a mapping table,
  or configuration), and which table/column pairs are declared. Tests use a synthetic mapping
  (`tenant-acme` → 527, `tenant-nova` → 528).

## D-003 rationale and impact

- Name alone cannot separate identical names in different legal entities; customer IDs would
  expose internal identifiers.
- The legal entity label comes from `d_legalentity.name` through the ontology's single
  confirmed join from the customer record. The label list is reviewed data
  (`planning/entity_choice_labels.json`) until the ontology carries label roles; the shared
  ontology source (outside this repo) was not changed.

## D-004 – D-006 rationale and impact

- Found by the Step 3 probe (see `TEST_EVIDENCE.md`): "total invoice amount" answered 397800.00,
  which was 327000.00 USD + 70800.00 INR; grouped totals dropped 3 customers; "total approved
  amount" hid 9 pending claims.
- Compatibility: scalar answers keep `result_type="scalar"` and `data.alias/value`, and add
  `data.currency` (and `data.missing_count` for SUM/AVG/MIN/MAX). Answer text ends with the
  currency code. A multi-currency total becomes `tabular` with one row per currency.
- Open: rounding/display precision (AVG shows 500.0000000000000000), currency conversion
  (none; no approved source) and whether "pending" should be reported differently per metric
  belong to the business metric catalog (A2.3).

## D-007 rationale and impact

- Before: a date condition became timestamp equality and answered "no value" (truth 3000.00).
- Now: "2026-04-20", "April 2026", "Q2 2026", "2026", ranges, before/after/since/until, today,
  yesterday, this/last month, quarter and year. The result states the exact period applied
  ("Payment date: 2026-04-01 to 2026-04-30 (US Central time, end date included).").
- Not supported (refused with the supported forms): weeks (start day undecided), rolling windows
  (is today included?), month without a year, fiscal periods.

## D-008 rationale and impact

- Same verification path for development and production; only key source and claim names
  change, so moving to Entra ID is a configuration change (see `docs/project/IDENTITY.md`).
- New dependency: PyJWT[crypto] 2.10.1 (with cryptography 44.0.2), needed to verify provider
  (RS256/JWKS) tokens correctly; a hand-written verifier would be a security risk.
- Compatibility: AUTH_MODE defaults to `api_key`, so existing development behaviour and tests are
  unchanged. `AskRequest` gains an optional `conversation_id`; `POST /clarify` exists in jwt mode only.
- Open: production membership source and tenant mapping; whether to keep the API key as an
  extra layer for service callers.

## D-002 follow-up: every table is scoped (2026-10-15)

- Found in the first live UI run: "total invoice amount" for tenant-acme returned 327000.00 USD,
  the sum of Acme, Nova and Vertex invoices. Only a *named* customer had been restricted to the
  company; tables without a name condition were read for every company. An earlier status
  message had said isolation was complete; that was wrong.
- Now (A2.4): every table in a plan, and inside EXISTS conditions, gets the company restriction,
  directly or through its owner (A-OWN-1). A table with no known owner is refused with
  "This information cannot be limited to your company yet". An unknown tenant is denied (403).
- Result: Acme total invoice amount is 129000.00 USD and 70800.00 INR (per currency, D-004).
- Compatibility: the scope applies only when `entity_scope_policy` is given (always in jwt mode).

## D-011 rationale and impact

- Owner choice "search both when unclear" differs from the handoff's default ("clarify instead of
  querying all backends"); chosen for a faster answer. The cost is measured: the benchmark marks such
  cases "extra source searched" (time and tokens), separately from "needed source missed".
- Rules-only result on the 30-question set: 26/30 exactly right, 0 needed sources missed; the 4
  others are unsure questions that go to both sources when no classifier answers.
- Compatibility: plain database answers keep their exact JSON. Routed answers add `route`
  ("database", "documents", "both") and, when documents were used, `passages`; `result_type` gains
  "documents". ROUTING_ENABLED=false restores the original path; ROUTE_CLASSIFIER_ENABLED=false gives
  rules only.
