# Test evidence

## 2026-10-14 — fresh baseline (before A2.1)

Branch `hardening/agent-reliability`, HEAD `d58f68e` plus uncommitted ambiguity patch.
Command: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` (live PostgreSQL + Neo4j from `.env`).

- Result: **179 passed, 11 failed, 48 warnings** (warnings: FastAPI `on_event` deprecation).
- All 11 failures had one cause: `EntityMatchGuard` returned `result_type="clarification"` for
  "Blue Prism" (repair-loop `TypeError` = `answer.data` is `None`).

## Blue Prism entity scope (read-only SQL, 2026-10-14)

| Customer ID | company_id (name) | legal_entity_id | Paid | Invoices | Claims |
|---|---|---|---|---|---|
| SYN-CUST-1001-01#527#1001 | 527 Acme Technologies | 1001 | 4000.00 | 4 | 500.00 |
| SYN-CUST-1002-01#527#1002 | 527 Acme Technologies | 1002 | 4000.00 | 4 | 1000.00 |
| SYN-CUST-1003-01#528#1003 | 528 Nova Healthcare | 1003 | 4000.00 | 4 | 500.00 |
| SYN-CUST-1004-01#528#1004 | 528 Nova Healthcare | 1004 | 4000.00 | 4 | 1000.00 |
| SYN-CUST-1005-01#529#1005 | 529 Vertex Manufacturing | 1005 | 4000.00 | 4 | 500.00 |
| SYN-CUST-1006-01#529#1006 | 529 Vertex Manufacturing | 1006 | 4000.00 | 4 | 1000.00 |
| **Sum** | 3 companies | 6 | **24000.00** | **24** | **4500.00** |

- All USD; `parent_id`, `hierarchy_id`, `level_1..4` are NULL for all 21 customers.
- Payment `company_id`/`legal_entity_id` agree with the customer row (no mismatches).
- The old expected values equal the cross-company sum. `FlowOne GQ doc.xlsx` (local only) has
  question text "Blue Prism(Customer)" and no reference SQL or expected values.
- `Blue Prism Technologies LE-1001` matches exactly one record; its invoices
  SYNINV000101 (1000.00 / 0.00 / CLOSE) … SYNINV000104 (8000.00 / 4900.00 / OPEN).

## 2026-10-14 — after A2.1

- Full: **196 passed, 0 failed, 48 warnings**.
- `tests/`: 151 passed. `packages/hybrid_contracts`: 45 passed.
- `tests/test_entity_match_regressions.py`: 21 passed (16 existing + 5 scope tests).

## 2026-10-14 — after A2.1b (tenant scope)

- Full: **213 passed, 0 failed, 48 warnings**. `tests/`: 156 passed. `packages/`: 57 passed
  (hybrid_contracts 45, tenant_scope 12).
- Portability: `packages/tenant_scope` tests run with `python -I -m pytest tests` from the package
  directory; importing it loads no FlowOne app modules (`agent`, `api`, `planning`, `database`, `config`).
- `tests/test_tenant_entity_scope.py` (live, read-only, synthetic tenant mapping): 5 passed —
  ambiguity inside company 527 clarifies; LE-1001 under tenant 527 = 4000.00; LE-1001 under the
  tenant mapped to 528 is "not found" (no cross-company leak); unmapped tenant raises before lookup.

## 2026-10-14 — after A1.2 (test separation and CI)

- Live classification: suite run with PostgreSQL/Neo4j pointed at a closed port → 61 failed,
  95 passed in `tests/`. Exactly those 61 now carry `integration` (18 whole modules; 9 tests in
  4 mixed modules). No assertions changed.
- Default `pytest`: 213 passed (unchanged). `-m integration`: 61 passed.
- CI conditions (fresh venv from `requirements.txt -c constraints.txt`, `pip check` clean,
  no `.env`, placeholder settings, cwd outside repo): `-m "not integration"` 152 passed,
  61 deselected; isolated `tenant_scope` run 12 passed.
- Not verified: the GitHub Actions workflow itself (not pushed; Linux runner not exercised).

## 2026-10-15 — after A2.2 (clarification flow)

- Full live suite: **251 passed, 0 failed** (64 integration).
- CI conditions (clean venv from `requirements.txt -c constraints.txt`, no `.env`, placeholder
  settings): `-m "not integration"` 187 passed. Isolated packages: tenant_scope 12, clarification_state 16.
- Guard: 32 passed (11 new: labelled sorted choices inside scope, not listed unless requested, too
  many matches, unlabelled choice, unconfirmed/duplicate/non-key label joins refused, selection
  re-checked with name+scope+key, no-longer-permitted selection refused, wrong key shape).
- Flow (`tests/test_entity_clarification_flow.py`): 8 unit + 3 live passed. Live, tenant mapped to
  company 527: options exactly "…LE-1001 — Acme Technologies - Primary Legal Entity" and
  "…LE-1002 — Acme Technologies - Secondary Legal Entity"; no `SYN-CUST` ID in the prompt;
  choosing LE-1001 → claims 500.00, LE-1002 → 1000.00 (matches read-only SQL); resume from
  another tenant and replay after use refused.
- `tools/audit_hardcoding.py`: no findings in changed production files.

## 2026-10-15 — Step 3 probe (before fixes)

14 unseen question shapes through the real pipeline (scripted LLM output only), compared with
independent read-only SQL.

| Case | Before | Reference | Verdict |
|---|---|---|---|
| Payments on 2026-04-20 (LE-1001) | "No aggregate value was available" (`payment_date = '2026-04-20'`) | 3000.00 | **Silent wrong** |
| Payments in 2026 | database error (`= '2026'`) | — | Error |
| Total invoice amount | "397800.00" | 327000.00 USD + 70800.00 INR | **Silent wrong (currency mix)** |
| Total approved claim amount | "6750.00" | 6750.00; 9 of 18 claims NULL | Hidden gap |
| Payments / claims by customer | 18 rows | 21 customers, 3 with none | Customers dropped |
| Invoices with claims, total | refused (fan-out) | 90000.00 | Safe but unanswered |
| Customers with claims, count | refused (fan-out) | 18 | Safe but unanswered |
| "Open" invoices | refused | 60 OPEN | Safe, unsupported |
| Invoice total, balance, AVG claim, MAX by customer, payments via invoices | correct | matches | OK |

Data facts: each invoice has at most one claim today (so the blocked fan-out queries would have
been right only by luck); payments' invoice and customer links never disagree (0 of 36), so the
two payment paths cannot be distinguished by this data.

## 2026-10-15 — after A3.1–A3.5

- Full live suite: **341 passed, 0 failed** (72 integration). CI conditions (clean venv, no
  `.env`/DB): 269 passed.
- Probe re-run: dates refused with a specific message; "total invoice amount" → "70800.00 INR;
  327000.00 USD"; single-currency totals end with the currency; invoices with claims → 90000.00
  USD via EXISTS; customers with claims → 18 via EXISTS, answer states "with related invoice and
  invoice claim records"; grouped results list 21 customers; approved total adds "9 invoice
  claim records with no approved amount are not included."
- Intended expected-value changes (all verified by read-only SQL): GQ004 18 → 21 rows (3 at 0);
  answer text gains the currency code in GQ001/GQ007/payment tests.
- `tools/audit_hardcoding.py`: no findings in changed production files.

## 2026-10-15 — status filters (A3.6/A3.7) and dates (A3.8)

Defects found and fixed (all values from read-only SQL):

| Case | Before | After |
|---|---|---|
| invoice status "open" | count 0 | 60 |
| invoice status "Unpaid" | count 0 | clarification: known values CLOSE, OPEN |
| "invoice status" resolution | refused in ~1 of 6 processes (hash-seed dependent) | inv_status under all 8 tested seeds |
| LE-1001 payments on 2026-04-20 | "no value" | 3000.00 USD, period stated |
| all payments 2026-04-21 to 2026-05-20 | — | 18000.00 (all at 05-20 15:00; end day included) |
| unclear period ("recently", "last week", "April") | — | refused with supported forms |

- Full live suite: **416 passed, 0 failed** (86 integration). CI conditions (clean venv, no DB):
  330 passed.
- Root cause of the flakiness: `_score_column` added the description bonus to a running maximum
  inside a loop over a `set` of variants; the result depended on `PYTHONHASHSEED`.

## 2026-10-15 — Step 4 (contracts 0.3.0, SQL retriever)

- Full live suite: **461 passed, 0 failed** (90 integration). CI conditions: 371 passed.
  Isolated packages: hybrid_contracts 60, tenant_scope 12, clarification_state 16; `pip check` clean.
- Live through the contract (read-only, synthetic tenant mapping): LE-1001 claims -> complete,
  Money 500.00 USD; "Blue Prism" with a conversation -> clarification with Acme's 2 choices; resume
  with LE-1002 -> complete 1000.00 USD; unmapped tenant -> denied; total invoice amount -> table
  USD 327000.00 / INR 70800.00.
- `/ask` JSON unchanged (exact-body test); internal outcome codes are never serialized.
- `tests/rag_host_stub.py` imports only `hybrid_contracts` and serves SQLRetriever and
  DisabledRetriever identically.

## 2026-10-15 — Step 5 (identity) and layer rules

- Full live suite: see final run below. CI conditions (clean venv, no DB): 429 passed before the
  layer test was added. Isolated packages: tenant_scope 12, clarification_state 16,
  trusted_identity 36; `pip check` clean.
- Rejected with 401: no token, garbage, expired, wrong key, wrong issuer/audience, no subject, no
  expiry, disallowed algorithm, `alg: none`, tampered payload. 403: spoofed tenant header, several
  companies without a choice. 422: tenant in body. 409: foreign/expired/replayed clarification.
- Live (dev tokens, example tenant mapping): Acme user "Blue Prism" -> Acme's 2 choices; Nova user
  cannot use Acme's choice (409); Acme picks LE-1002 -> 1000.00 USD; replay 409; Nova user asking
  for LE-1001 -> "could not find"; Acme user -> 500.00 USD.
- Layer rules: 32 checks pass (imports point down; business-rule folders never read config;
  packages import no application code).

## 2026-10-15 — Step 7 (bounded execution, least privilege)

- Full live suite: **588 passed, 0 failed**. CI conditions (clean venv, no DB): 490 passed in ~8 s.
- Live: a no-op UPDATE handed to the executor with forged approvals -> PostgreSQL
  ReadOnlySqlTransaction; `pg_sleep(5)` under a 0.5 s deadline -> cancelled, DeadlineExceeded in
  < 2.5 s; statement timeout without deadline -> plain TimeoutError (no SQL text); pooled
  connection after a session `SET` -> default restored.
- Current login privileges (read-only check): role `postgres`, superuser, bypasses RLS, can
  modify 50/50 tables, can CREATE; TLS off. Production start-up refuses all of these.
- Passwords with spaces, quotes or `sslmode=disable host=evil` stay a password (no option injection).
- Unreachable Neo4j: ConnectionError in ~3 s (previously the test run took minutes).

## 2026-10-15 — running as the read-only login

- `docs/sql/flowone_readonly_role.sql` applied by the owner (re-runnable version). App login:
  `flowone_reader`, not superuser, no row-security bypass, 0 writable tables, cannot CREATE.
  Remaining local finding: TLS not required (expected locally; enforced in production).
- Full live suite as `flowone_reader`: **588 passed, 0 failed**. Two pooled-connection tests had
  hard-coded "read-only = off" as the default; they now compare against the login's own defaults
  (a read-only login defaults to "on").

## 2026-10-15 — UI Phase 1 (A13.1, A13.2)

- UI: 41 tests passed (vitest, fake API): formatting without rounding or float conversion,
  client error mapping (401/403/404/409/429/503/504/400/422/500/network), currency rows never
  summed, hostile HTML/script shown as text, choices locked after use, session expiry, double
  submit ignored, late answer dropped after workspace switch, no stuck "busy" after a new
  conversation, expired choice (409), too slow (504) with reference, 403 refreshes workspaces,
  documents shown as not available. `tsc` clean; production build 157 kB (51 kB gzip).
- API: GET /me and CORS tests (6) pass; trusted_identity 38 pass.
- Not verified: the UI against the live API with a real LLM.

## 2026-10-15 — tenant isolation fix (A2.4) and chosen-record answers (A2.5)

- Leak reproduced live: tenant-acme "total invoice amount" = 327000.00 USD (all three companies).
  After the fix: 129000.00 USD + 70800.00 INR, matching independent read-only SQL for company 527.
- `tests/test_tenant_plan_scope.py`: 21 passed (direct, linked, EXISTS, unmapped table refused,
  unknown tenant denied, empty groups kept with the filter in the join).
- A2.5: after choosing "Blue Prism Technologies LE-1001 — …" the answer reads
  "… for Blue Prism Technologies LE-1001 — Acme Technologies - Primary Legal Entity is 500.00 …"
  (was "for Blue Prism"). Tests: 3 answer-service, 1 orchestrator (labels kept across two
  successive choices), live check on both records (3 integration passed).
- Full suite: **626 passed, 0 failed** (live, as `flowone_reader`).
- Owner UI check (tenant-nova): total invoice amount 99000.00 USD (independent SQL: 24 invoices, 99000.00 USD); Blue Prism choices only LE-1003/LE-1004 (Nova); chosen-record wording confirmed. Independent per-company totals: 527 = 129000.00 USD + 70800.00 INR, 528 = 99000.00 USD, 529 = 99000.00 USD (sum 327000.00 = the old leaked figure).
- Owner UI check (tenant-acme): "How many open invoices are there?" = 24 (independent SQL: company 527
  inv_status OPEN 24, CLOSE 6). "What is the total payment amount in April 2026?" = 18000.00 USD with
  the period note 2026-04-01 to 2026-04-30 (independent SQL: 6 payments, 18000.00 USD; Nova and Vertex
  each also 18000.00 USD, excluded). Both had failed in the first live run (unmapped table / no scope).

## 2026-10-15 — Step 8 document ingestion (A8.1–A8.5)

- `packages/document_ingestion` in an isolated interpreter (`python -I`): **64 passed**
  (43 extraction: renamed files, macros, password/old formats, zip bomb (20 MB of zeros in a
  <100 kB file), XML entity bomb, damaged files, scanned and partly scanned PDFs, permissions-only
  PDF password, hidden Word text (direct and by style), hidden sheets/rows/columns, saved formula
  values and missing ones, merged table cells, hyperlinks, invisible characters, instructions
  inside each format; 21 service: lifecycle, isolation, duplicates, replacement order, delete
  during processing, retry rules, restart recovery).
- `tests/test_document_adapters.py` **22 passed** (path escapes refused, tenant filter on every
  catalog query, atomic index replace, FTS search, hanging reader stopped after 3 s, crashing
  reader contained). `tests/test_document_api.py` **16 passed** (401 on every route, other
  workspace 404, tenant header outside token 403, 411/413 before reading, safe 500).
- Full backend suite (live, `flowone_reader`): **729 passed, 0 failed**. UI: 41 passed.
- Clean environment (fresh venv, no `.env`, pinned installs, `pip check` clean): packages
  17 + 16 + 38 + 64 passed; database-free suite 626 passed.
- Live smoke through `api.runtime` on a second instance (port 8002; the owner's 8001 untouched),
  real `.env` settings: Word, PDF (one scanned page) and Excel (one hidden sheet) became ready
  with the expected notes; scanned PDF failed with the scanned message; renamed file refused (400);
  Nova token saw no Acme documents (list empty, detail 404); duplicate detected; all four deleted
  (204) leaving 0 passages and 0 files. Server log: no errors.

## 2026-10-16 — Documents panel in the UI (A13.4)

- UI: **57 passed** (17 new: upload and follow to ready, local refusals for type/empty/over 25 MB,
  server refusal with reference, duplicate notice, retry only when allowed, delete only after
  confirmation, hostile file names shown as text, no workspace chosen, late reply from the previous
  workspace dropped, session end; client: multipart without manual Content-Type, DELETE 204, 413,
  bare "Not Found"). `tsc` clean; production build 163 kB (53 kB gzip).
- Live: upload through the UI dev proxy (5175) to the owner's API (8001): 202 queued, then ready,
  then deleted (204).

## 2026-10-16 — Step 9 document retrieval (A9.1–A9.3)

- `packages/document_retrieval` isolated: **18 passed** (search words cannot carry search syntax;
  hit from another workspace, deleted document or old version never shown; budget respected;
  failures map to safe statuses; instructions inside passages returned unchanged as data).
- `tests/test_document_search.py` **18 passed**: word endings match, citations name file and place
  (page / section and paragraphs / sheet and rows), better matches first, Acme and Nova with the same
  words see only their own passages, replacement shows only version 2, deletion removes hits, stale
  index rows dropped, locked index reports unavailable, index upgrade keeps existing passages; route:
  401, 422 on empty/oversized/extra fields (no tenant in the body), clarification for filler-only
  questions.
- Full backend suite: **763 passed** after fixing a timing test (`test_nested_deadline_cannot_extend_the_outer_one`)
  that failed once on Windows floating-point rounding (0.1000000000003638 <= 0.1); the check now
  allows 1e-9 for rounding only. Isolated packages: 17 + 16 + 38 + 64 + 18 passed. UI: **63 passed**.
- Live (second instance on port 8002, temporary document folder; owner's 8001 untouched): Acme
  "What is the late payment fee?" -> acme_terms.docx · section "Payment terms", paragraphs 1–2;
  "CL-77 amount" -> acme_claims.xlsx · sheet "Claims", rows 1–2; Nova "late payment fee" -> only
  nova_terms.pdf; "warranty" -> empty with message; "what is the" -> clarification; after delete ->
  empty. Server log: no errors.

## 2026-10-16 — Step 11 routing (A11.1–A11.4)

- `packages/question_router` isolated: **26 passed**. `tests/test_hybrid_assistant.py` **29 passed**
  (database-only path unchanged and no classifier call; documents-only; combined in parallel (two 0.4 s
  steps < 0.75 s); unsure -> one classifier call; unclear -> both; no documents -> note and no query;
  broken document store does not stop database questions; no fallback from an unanswerable database
  question; combined leads with documents when the database cannot interpret; record choices kept;
  failed part reported; both failed -> error; denied part -> whole question denied; shared deadline;
  workspace passed through; resume; classifier labels and failure; question passed as data; `/ask` over HTTP).
- Contract: three existing tests (`test_ask_endpoint_shape_is_unchanged`,
  `test_internal_outcome_never_reaches_the_ask_json`, `test_clarification_over_http`) failed when the new
  fields were always serialized; fixed in the model (fields omitted when unset), tests unchanged.
- Routing benchmark offline (`tests/test_routing_benchmark.py`): **32 passed**; dry run 26/30 exactly
  right, 0 needed sources missed, 4 unsure questions -> both (extra source).
- Full backend suite: **851 passed**. Isolated packages: 17 + 16 + 38 + 64 + 18 + 26 passed. UI: **67 passed**.
- Not yet measured: live AI calls, tokens and p50/p95 (needs `--live`, costs tokens; awaiting owner approval).
