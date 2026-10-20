# Roadmap: 14 steps, status and next best actions

Updated 2026-10-15. Detailed actions: `ACTION_REGISTER.md`. Decisions: `DECISION_LOG.md`.

| Step | What | Status | Notes |
|------|------|--------|-------|
| 1 | Baseline, reproducible tests, CI | Done | A1.3 done 2026-10-20: integration tests run in CI on fixtures |
| 2 | Customer scope, clarification flow, tenant isolation of every table | Done | A2.4 leak fix (2026-10-15); A-OWN-1 confirmed 2026-10-19; A2.3 metric catalog open |
| 3 | SQL correctness (dates, currency, status, empty groups, missing values, EXISTS) | Done | A3.9 remaining items (entity matching, path ranking, composite keys) |
| 4 | Shared contracts 0.3.0, SQL adapter | Done | |
| 5 | Caller identity (dev tokens now, Entra ID by configuration) | Done | Production membership source open |
| 6 | Role permissions | **Postponed** (owner, 2026-10-15) | Required before real users or real data |
| 7 | Bounded execution, least privilege | Done | App runs as `flowone_reader`; change its password before real data |
| 13a | **Chat UI, Phase 1 (pulled forward, D-009)** | Done | SQL answers only; 41 UI tests; first live run done (found A2.4) |
| 8 | Document ingestion (Word, PDF, Excel) | Done (2026-10-15) | D-010; upload API ready; searching passages is Step 9 |
| 9 | Document retrieval | Done (2026-10-16) | Quoted passages with citations; search in the UI |
| 13b | Chat UI, Phase 2 | Done (2026-10-19, with A12d) | Document evidence and citations in answers, saved conversations |
| 10 | Graph evidence retrieval | Later | Graph route stays off until then | |
| 11 | Routing and hybrid coordination | Done (live benchmark 2026-10-19) | D-011; one chat question answered from database, documents or both | |
| 12 | Grounded answers, saved conversations | Done (2026-10-19) | D-012; written and combined answers off by default, development only; benchmark meets p50/p95 overall, two-part questions p95 11.2 s |
| 13c | UI completion | | Browser end-to-end tests, all flows |
| 14 | Release evidence, deployment | | |

## Next best actions

1. Done 2026-10-15: live UI re-test passed (per-company totals, chosen-record wording, open invoices, April 2026).
2. Done 2026-10-19: ownership A-OWN-1 confirmed, 5 tables added; 2 ontology links to confirm (see ACTION_REGISTER P1).
3. Done 2026-10-19: live routing benchmark; target p50 < 3 s, p95 < 10 s (A11.5).
4. Done 2026-10-19: Step 12 (D-012). Done 2026-10-20: test database for CI (A1.3). Next: Step 6 permissions before real users.
5. Before any real user: Step 6 permission matrix; random `flowone_reader` password; TLS.

## UI Phase 1 scope (D-009)

Separate `ui/` project (React + Vite + TypeScript). Talks **only** to the public HTTP API
(`/ask`, `/clarify`, `/me`, `/health`); never imports backend code; never decides permissions.

Adjusted from the owner's mock-up so the screen never shows what the system cannot back up:

| Mock-up | Phase 1 behaviour |
|---------|-------------------|
| Answer for "Blue Prism" directly | Clarification buttons first (D-001), then the answer names the record |
| "$24,000" | Amount with currency code ("24,000.00 USD"); one line per currency (D-004) |
| "Disputed" badge | Real stored statuses only, until the metric catalog defines "disputed" (A2.3) |
| Document quotes, upload panel | Database evidence only; documents panel shows "Not available yet" (no placeholder content) |
| Workspace switch | Starts a fresh conversation; nothing from the previous company is reused |
| Conversation list | Saved on the server per user and company (Step 12c/12d) |
| Role in footer | Displayed only; hiding by role is the server's job (Step 6) |

## Negative scenarios the UI must handle (without changing the architecture)

| Scenario | UI behaviour | Architecture rule protected |
|----------|--------------|-----------------------------|
| Token missing / expired / invalid (401) | Back to sign-in with a message; the conversation stays in memory | Identity only from verified tokens |
| Not a member of selected company (403) | Message; workspace list refreshed from `/me` | Tenant chosen from verified memberships only |
| Several companies, none chosen | Workspace picker shown first | Explicit active tenant |
| Workspace switched while a request is running | Late answer discarded, not shown in the new workspace | No cross-tenant reuse |
| Clarification expired / used / foreign (409) | "Please ask again"; buttons disabled | Choices bound and single-use (server) |
| Question too slow (504) | "Took too long" with retry | One deadline per question |
| Dependency down (503), rate limited (429), server error (500) | Generic message with request ID | Errors never show internals |
| Network down / CORS refused | "Cannot reach the service" | — |
| Double click on Send / buttons | Ignored while a request is in flight | No duplicate execution |
| Answer text containing HTML or script | Shown as plain text, never rendered as HTML | Documents and data are untrusted |
| Very long tables, truncated results | Scrollable table + "first N rows" notice | Server row limits |
| Several currencies, unknown currency, empty groups, missing values, date period notes | Shown as returned, with notices | UI never recalculates numbers |
| Unsupported question / date form | Shown as a clarification-style message | Refuse rather than guess |
| Token in the page | Kept in memory only (lost on reload in Phase 1); never in localStorage | Tokens not exposed to other scripts |
| Backend in api_key mode | UI requires jwt mode and says so | Per-user identity |
